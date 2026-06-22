"""Helpers for invoking Foundry Hosted Agent runtimes.

Supports two invocation modes, selected automatically based on configuration:

Direct HTTP mode (Docker Compose / local dev):
  Triggered when HOSTED_AGENT_*_URL is set, for example http://agent-clinical:8000.
  Calls POST {url}/responses using the Foundry Responses API envelope.
  Used by docker-compose where each agent runs as a local container.

Foundry Hosted Agents mode (Azure deployment via azd up):
  Triggered when HOSTED_AGENT_*_URL is empty and AZURE_AI_PROJECT_ENDPOINT is set.
  Calls the dedicated hosted-agent Responses endpoint directly:
    {project_endpoint}/agents/{agentName}/endpoint/protocols/openai/responses
  Auth uses DefaultAzureCredential, which resolves to the backend ACA managed identity.

Why raw HTTP for Foundry mode:
  Hosted Agents must be called through the agent-specific endpoint. This file
  avoids passing model=, agent=, or agent_reference= to responses.create().
  It also handles both normal JSON responses and unexpected text/event-stream
  responses defensively so backend parsing does not hide the real agent error.
"""

import asyncio
import json
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _build_direct_headers() -> dict[str, str]:
    """Build headers for direct HTTP mode (docker-compose). Supports optional token."""
    headers = {"Content-Type": "application/json"}
    if settings.HOSTED_AGENT_AUTH_TOKEN:
        value = settings.HOSTED_AGENT_AUTH_TOKEN
        if settings.HOSTED_AGENT_AUTH_SCHEME:
            value = f"{settings.HOSTED_AGENT_AUTH_SCHEME} {value}"
        headers[settings.HOSTED_AGENT_AUTH_HEADER] = value
    return headers


def _extract_result(data: Any) -> dict:
    """Parse a Foundry Responses API reply into a plain result dict.

    Expected non-streaming shape:
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "text", "text": "<json string>"}]
                }
            ]
        }

    The agent emits structured output, so the text should be a JSON-serialized
    Pydantic model. This function falls back gracefully if the response shape is
    unexpected.
    """
    if not isinstance(data, dict):
        return {"error": "Agent returned a non-object response", "tool_results": []}

    # Foundry/OpenAI error objects can appear directly in a successful transport response.
    if isinstance(data.get("error"), dict):
        error_obj = data["error"]
        message = error_obj.get("message") or str(error_obj)
        return {"error": message, "tool_results": []}

    if isinstance(data.get("error"), str):
        return {"error": data["error"], "tool_results": []}

    status = data.get("status", "")
    if status not in ("completed", ""):
        error_obj = data.get("error", {})
        if isinstance(error_obj, dict) and error_obj.get("message"):
            error_detail = f"Agent returned status={status!r}: {error_obj['message']}"
        else:
            error_detail = f"Agent returned status={status!r}"

        logger.warning(
            "Agent response status=%r (not 'completed'). Error: %s. "
            "Response keys: %s. Full response (truncated): %s",
            status,
            error_obj,
            list(data.keys()),
            str(data)[:2000],
        )
        return {"error": error_detail, "tool_results": []}

    # Some adapters expose the final text directly.
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        try:
            parsed = json.loads(output_text)
            if isinstance(parsed, dict):
                return parsed
            return {
                "error": f"Agent returned JSON, but it was not an object: {type(parsed).__name__}",
                "tool_results": [],
            }
        except (json.JSONDecodeError, TypeError):
            return {"error": f"Agent output_text was not valid JSON: {output_text[:500]}"}

    # Standard Responses API output array.
    output = data.get("output", [])
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict):
            continue

        content = item.get("content", [])
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue

            # Common Responses text block shape.
            if block.get("type") in ("text", "output_text"):
                text = block.get("text", "")
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        return parsed
                    return {
                        "error": f"Agent returned JSON, but it was not an object: {type(parsed).__name__}",
                        "tool_results": [],
                    }
                except (json.JSONDecodeError, TypeError):
                    return {"error": f"Agent text was not valid JSON: {text[:500]}"}

    # Fallback: some local adapters return the result directly under known keys.
    for key in ("result", "data"):
        value = data.get(key)
        if isinstance(value, dict):
            return value

    return {"error": f"Could not extract result from agent response: {str(data)[:500]}"}


def _parse_json_text_as_result(text: str, source: str) -> dict:
    """Parse a JSON string expected to contain the agent's structured result."""
    if not text:
        return {"error": f"{source} was empty", "tool_results": []}

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"error": f"{source} was not valid JSON: {text[:500]}", "tool_results": []}

    if isinstance(parsed, dict):
        return parsed

    return {
        "error": f"{source} parsed as JSON, but was not an object: {type(parsed).__name__}",
        "tool_results": [],
    }


def _parse_sse_response_text(raw_text: str) -> dict:
    """Parse a text/event-stream response body into the final useful payload.

    This is defensive. Foundry mode requests stream=false and Accept=application/json,
    but if an agent/runtime returns SSE anyway, this prevents the backend from losing
    the real response or error.

    Handles common event shapes:
      - data: {"output_text": "..."}
      - data: {"response": {"output": [...]}}
      - data: {"type": "response.output_text.delta", "delta": "..."}
      - data: {"type": "response.output_text.done", "text": "..."}
      - data: {"error": {...}}
    """
    events: list[dict[str, Any]] = []
    output_text_parts: list[str] = []
    completed_response: dict[str, Any] | None = None

    for block in raw_text.split("\n\n"):
        event_name = ""
        data_lines: list[str] = []

        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith(":"):
                continue

            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
                continue

            if line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())

        if not data_lines:
            continue

        data_text = "\n".join(data_lines)
        if data_text == "[DONE]":
            continue

        try:
            data_obj: Any = json.loads(data_text)
        except json.JSONDecodeError:
            data_obj = {"text": data_text}

        events.append({"event": event_name, "data": data_obj})

        if not isinstance(data_obj, dict):
            continue

        if data_obj.get("error"):
            return {"error": str(data_obj["error"]), "tool_results": []}

        # OpenAI Responses streaming event shapes.
        event_type = data_obj.get("type") or event_name

        if event_type == "response.output_text.delta" and isinstance(data_obj.get("delta"), str):
            output_text_parts.append(data_obj["delta"])

        if event_type == "response.output_text.done" and isinstance(data_obj.get("text"), str):
            return _parse_json_text_as_result(data_obj["text"], "Agent streaming output_text")

        if event_type == "response.completed" and isinstance(data_obj.get("response"), dict):
            completed_response = data_obj["response"]

        # Some gateways return the completed response directly in a data envelope.
        if isinstance(data_obj.get("response"), dict):
            completed_response = data_obj["response"]

        if isinstance(data_obj.get("output_text"), str):
            return _parse_json_text_as_result(data_obj["output_text"], "Agent output_text")

    if output_text_parts:
        return _parse_json_text_as_result(
            "".join(output_text_parts),
            "Agent streaming output_text",
        )

    if completed_response:
        return _extract_result(completed_response)

    # Last pass: inspect all event payloads from the end for a parseable response.
    for item in reversed(events):
        data = item.get("data")
        if not isinstance(data, dict):
            continue

        extracted = _extract_result(data)
        if not extracted.get("error"):
            return extracted

    return {
        "error": f"Could not extract result from SSE response: {raw_text[:1000]}",
        "tool_results": [],
    }


def _parse_foundry_http_response(response: httpx.Response, agent_name: str) -> dict:
    """Parse a Foundry Hosted Agent response whether it is JSON or SSE."""
    content_type = response.headers.get("content-type", "").lower()
    raw_text = response.text or ""

    if response.status_code >= 400:
        return {
            "error": (
                f"Foundry Hosted Agent {agent_name} call failed "
                f"({response.status_code}): {raw_text[:2000]}"
            ),
            "tool_results": [],
        }

    if "text/event-stream" in content_type:
        return _parse_sse_response_text(raw_text)

    try:
        data = response.json()
    except ValueError:
        # Some infrastructure can return SSE without the expected content type.
        if raw_text.lstrip().startswith("event:") or "\ndata:" in raw_text:
            return _parse_sse_response_text(raw_text)

        return {
            "error": (
                f"Foundry Hosted Agent {agent_name} returned non-JSON response: "
                f"{raw_text[:1000]}"
            ),
            "tool_results": [],
        }

    return _extract_result(data)


async def _invoke_direct_http(agent_name: str, url: str, payload: dict) -> dict:
    """Invoke agent via direct HTTP for Docker Compose / local dev mode.

    Uses the Foundry Responses API envelope expected by from_agent_framework().
    Input must be a flat array of message objects, not wrapped in a {messages: []} dict.
    """
    request_body = {
        "input": [{"type": "message", "role": "user", "content": json.dumps(payload)}],
        "stream": False,
    }
    responses_url = url.rstrip("/") + "/responses"

    try:
        timeout = httpx.Timeout(settings.HOSTED_AGENT_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(
            timeout=timeout, headers=_build_direct_headers()
        ) as client:
            response = await client.post(responses_url, json=request_body)
            response.raise_for_status()
            data = response.json()
            result = _extract_result(data)
            logger.info(
                "Hosted %s invocation succeeded via %s", agent_name, responses_url
            )
            return result
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        logger.warning("Hosted %s invocation failed: %s", agent_name, detail)
        return {
            "error": f"Hosted {agent_name} call failed ({status_code}): {detail}",
            "tool_results": [],
        }
    except Exception as exc:
        logger.warning("Hosted %s invocation failed: %s", agent_name, exc)
        return {
            "error": f"Hosted {agent_name} call failed: {exc}",
            "tool_results": [],
        }


async def _invoke_foundry_agent(
    agent_name: str, foundry_agent_name: str, payload: dict
) -> dict:
    """Invoke a Foundry Hosted Agent through its dedicated Responses endpoint.

    The agent name is part of the endpoint path. Do not pass agent_reference,
    model, or agent inside the request body.
    """
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        return {
            "error": (
                f"Failed to initialise Azure credential for {agent_name}: {exc}. "
                "Install with: pip install azure-identity"
            ),
            "tool_results": [],
        }

    project_endpoint = settings.foundry_project_endpoint
    responses_url = (
        f"{project_endpoint}/agents/{foundry_agent_name}"
        f"/endpoint/protocols/openai/responses?api-version=v1"
    )

    try:
        token = await asyncio.to_thread(
            DefaultAzureCredential().get_token,
            "https://ai.azure.com/.default",
        )
    except Exception as exc:
        return {
            "error": f"Failed to get Azure AI token for {agent_name}: {exc}",
            "tool_results": [],
        }

    headers = {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Foundry-Features": "HostedAgents=V1Preview",
    }

    # Use simple non-streaming input for the hosted-agent Responses endpoint.
    # The agent receives this JSON string as the user message content.
    request_body = {
        "input": json.dumps(payload),
        "stream": False,
    }

    try:
        timeout = httpx.Timeout(
            connect=30.0,
            read=max(float(settings.HOSTED_AGENT_TIMEOUT_SECONDS), 240.0),
            write=30.0,
            pool=30.0,
        )

        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            response = await client.post(responses_url, json=request_body)

        # Temporary high-value log while debugging hosted agent runtime shape.
        # Keep truncated to avoid logging PHI-sized payloads.
        logger.warning(
            "Foundry raw response for %s: status=%s content_type=%s body=%s",
            agent_name,
            response.status_code,
            response.headers.get("content-type"),
            response.text[:3000],
        )

        result = _parse_foundry_http_response(response, agent_name)

        # Capture Foundry correlation IDs for the Debug Console (session
        # logstream + App Insights trace lookup). Underscore-prefixed so they
        # are read by the orchestrator trace builder but dropped by Pydantic
        # when the agent result is parsed into the typed response model.
        if isinstance(result, dict):
            session_id = response.headers.get("x-agent-session-id", "")
            if session_id:
                result.setdefault("_foundry_session_id", session_id)
            try:
                envelope = response.json()
                if isinstance(envelope, dict) and envelope.get("id"):
                    result.setdefault("_foundry_response_id", str(envelope["id"]))
            except ValueError:
                pass

        if result.get("error"):
            logger.warning(
                "Foundry Hosted Agent %s (%s) returned error: %s",
                agent_name,
                foundry_agent_name,
                result["error"],
            )
        else:
            logger.info(
                "Foundry Hosted Agent %s (%s) invocation succeeded",
                agent_name,
                foundry_agent_name,
            )

        return result

    except httpx.TimeoutException:
        logger.warning(
            "Foundry Hosted Agent %s (%s) timed out calling %s",
            agent_name,
            foundry_agent_name,
            responses_url,
        )
        return {
            "error": (
                f"Foundry Hosted Agent {agent_name} call timed out. "
                "This may be cold start, long MCP/tool execution, or an agent runtime hang."
            ),
            "tool_results": [],
        }
    except Exception as exc:
        detail = str(exc)[:1000]
        logger.warning("Foundry %s invocation failed: %s", agent_name, detail)
        return {
            "error": f"Foundry Hosted Agent {agent_name} call failed: {detail}",
            "tool_results": [],
        }


async def invoke_hosted_agent(
    agent_name: str,
    url: str,
    payload: dict,
    foundry_agent_name: str = "",
) -> dict:
    """Invoke a hosted MAF agent and dispatch between Docker Compose and Foundry.

    Args:
        agent_name: Display name for logging, for example "clinical-reviewer-agent".
        url: Direct HTTP URL set by docker-compose. Empty string for Foundry mode.
        payload: Request data dict forwarded to the agent.
        foundry_agent_name: Foundry Hosted Agent name, for example
            "clinical-reviewer-agent". Required when url is empty and Foundry
            mode is active.

    Mode selection:
        url is set   -> Direct HTTP (Docker Compose / local dev)
        url is empty -> Foundry Hosted Agents mode (requires AZURE_AI_PROJECT_ENDPOINT)
    """
    if url:
        return await _invoke_direct_http(agent_name, url, payload)

    if settings.foundry_project_endpoint and foundry_agent_name:
        return await _invoke_foundry_agent(agent_name, foundry_agent_name, payload)

    return {
        "error": (
            f"{agent_name} is not reachable: set either HOSTED_AGENT_*_URL "
            "for Docker Compose or AZURE_AI_PROJECT_ENDPOINT for Foundry Hosted Agents."
        ),
        "tool_results": [],
    }
