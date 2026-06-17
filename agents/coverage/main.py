"""Coverage Assessment Hosted Agent — MAF entry point.

Verifies provider NPI, searches Medicare coverage policies via CMS MCP,
maps clinical findings to policy criteria with MET/NOT_MET/INSUFFICIENT
assessment, and returns a structured coverage evaluation.

Deployed as a Foundry Hosted Agent via azure.ai.agentserver.
MCP tools are consumed through a Foundry Toolbox (coverage-tools): hosted agents
can reach the Foundry project domain but NOT arbitrary public internet, so the
toolbox proxies out to the medical-data MCP servers from Foundry's own network
(see scripts/create_toolbox.py and the TOOLBOX_ENDPOINT env set by
scripts/register_agents.py). Structured output enforced via
default_options={"response_format": CoverageResult}.
"""
import asyncio
import contextlib
import inspect
import json
import logging
import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from agent_framework import MCPStreamableHTTPTool, SkillsProvider
from agent_framework.azure import AzureOpenAIResponsesClient
from agent_framework.exceptions import ToolExecutionException
from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponsesAgentServerHost,
    TextResponse,
)
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from mcp.shared.exceptions import McpError

from schemas import CoverageResult

load_dotenv(override=True)  # override=True required for Foundry-deployed env vars

logger = logging.getLogger(__name__)


# Shared Azure credential (managed identity in hosted runtime, az CLI locally).
_CREDENTIAL = DefaultAzureCredential()


class _ToolboxAuth(httpx.Auth):
    """Inject the Foundry bearer token + preview header on every toolbox request.

    Hosted Foundry agents can reach the Foundry project domain but NOT arbitrary
    public internet, so MCP tools are consumed through a Foundry Toolbox endpoint
    (on the project domain) that proxies out to the real MCP servers. Every
    request needs an AAD bearer token (scope https://ai.azure.com/.default) and
    the Toolboxes preview feature header.
    """

    def __init__(self, token_provider):
        self._token_provider = token_provider

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self._token_provider()}"
        request.headers["Foundry-Features"] = "Toolboxes=V1Preview"
        yield request


# Reused across requests; the per-request toolbox tool instance wraps it.
_TOOLBOX_HTTP_CLIENT = httpx.AsyncClient(
    auth=_ToolboxAuth(
        get_bearer_token_provider(_CREDENTIAL, "https://ai.azure.com/.default")
    ),
    timeout=httpx.Timeout(120.0),
)


def _mcp_warning(tool_label: str, tool_name: str, exc: Exception) -> str:
    """Return a structured warning payload instead of crashing the agent run."""
    detail = str(exc)[:1000] or exc.__class__.__name__
    return json.dumps(
        {
            "tool_name": tool_label,
            "called_tool": tool_name,
            "status": "warning",
            "detail": (
                f"{tool_label} MCP tool unavailable during hosted-agent runtime: "
                f"{detail}. Continue with conservative manual-review findings."
            ),
        }
    )


def _host_reachable(url: str, timeout: float = 3.0) -> bool:
    """Quick DNS + TCP probe so we never attach an unreachable toolbox endpoint.

    If the toolbox host can't be reached at startup we run tool-less and let the
    handler degrade to a schema-valid HTTP 200 rather than hanging into a 500.
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return False
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError as exc:
        logger.warning("Toolbox host unreachable: %s (%s)", url, exc)
        return False


def _degraded_coverage_result(detail: str) -> str:
    """Schema-valid CoverageResult emitted when the agent run cannot complete.

    Keeps the hosted agent returning HTTP 200 with a conservative manual-review
    payload instead of a 500 when the model call or MCP tools fail.
    """
    return json.dumps(
        {
            "agent_name": "Coverage Agent",
            "checks_performed": [],
            "provider_verification": None,
            "coverage_policies": [],
            "criteria_assessment": [
                {
                    "criterion": "Automated coverage assessment",
                    "status": "INSUFFICIENT",
                    "confidence": 0,
                    "evidence": [],
                    "notes": (
                        "Coverage tools or the model call were unavailable; "
                        "route to manual coverage review."
                    ),
                    "source": "degraded",
                    "met": False,
                }
            ],
            "coverage_criteria_met": [],
            "coverage_criteria_not_met": [],
            "policy_references": [],
            "coverage_limitations": [],
            "documentation_gaps": [],
            "tool_results": [
                {
                    "tool_name": "coverage-tools",
                    "status": "warning",
                    "detail": f"Agent run degraded: {detail[:500]}",
                }
            ],
            "error": f"degraded: {detail[:500]}",
        }
    )


class _ToolboxMCPTool(MCPStreamableHTTPTool):
    """Toolbox MCP tool that reconnects on session expiry and degrades on error.

    Returns a structured warning instead of raising so an upstream tool failure
    never crashes the hosted agent run. Reconnects once on MCP 'Session
    terminated' (idle toolbox sessions are reaped server-side).
    """

    async def call_tool(self, tool_name: str, **kwargs) -> str:
        try:
            return await super().call_tool(tool_name, **kwargs)
        except ToolExecutionException as exc:
            cause = exc.__cause__
            if isinstance(cause, McpError) and "Session terminated" in str(cause):
                logger.info("Toolbox MCP session expired for %s; reconnecting", self.name)
                try:
                    await self.connect(reset=True)
                    return await super().call_tool(tool_name, **kwargs)
                except Exception as retry_exc:  # noqa: BLE001
                    logger.exception(
                        "Toolbox tool %s.%s failed after reconnect", self.name, tool_name
                    )
                    return _mcp_warning(self.name, tool_name, retry_exc)
            logger.exception("Toolbox tool %s.%s failed", self.name, tool_name)
            return _mcp_warning(self.name, tool_name, exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Toolbox tool %s.%s failed", self.name, tool_name)
            return _mcp_warning(self.name, tool_name, exc)


def _agent_result_to_text(result: Any) -> str:
    """Extract text/JSON from an Agent Framework run result."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return json.dumps(result)

    model_dump_json = getattr(result, "model_dump_json", None)
    if callable(model_dump_json):
        return str(model_dump_json())

    for attr in ("text", "output_text", "content"):
        value = getattr(result, attr, None)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return json.dumps(value)
        value_dump_json = getattr(value, "model_dump_json", None)
        if callable(value_dump_json):
            return str(value_dump_json())

    return str(result)


async def _run_agent_for_responses(agent: Any, input_text: str) -> str:
    """Run an Agent Framework agent from the Responses protocol handler."""
    result = agent.run(input_text)
    if inspect.isawaitable(result):
        result = await result
    return _agent_result_to_text(result)


async def _extract_input_text(request: CreateResponse, context: ResponseContext) -> str:
    """Extract user input from the Responses request with raw-string fallback."""
    input_text = await context.get_input_text()
    if input_text:
        return input_text

    raw_input = getattr(request, "input", "")
    if isinstance(raw_input, str):
        return raw_input
    if raw_input:
        return json.dumps(raw_input)
    return ""


def main() -> None:
    # --- Observability: env var setup for Foundry agentserver adapter ---
    _ai_conn = os.environ.get("APPLICATION_INSIGHTS_CONNECTION_STRING") or os.environ.get("MONITORING_CONNECTION_STRING")
    if _ai_conn:
        os.environ.setdefault("APPLICATIONINSIGHTS_CONNECTION_STRING", _ai_conn)
        print("[observability] App Insights connection string set for agent-coverage")
    else:
        print("[observability] APPLICATION_INSIGHTS_CONNECTION_STRING not set — telemetry disabled")
    os.environ.setdefault("OTEL_SERVICE_NAME", "agent-coverage")

    # --- Foundry Toolbox endpoint ---
    # Hosted agents reach the Foundry project domain but not arbitrary public
    # internet, so NPI/CMS-coverage MCP tools are consumed through the
    # coverage-tools toolbox. The toolbox tool INSTANCE is created per request
    # (see handler) so its streamable-HTTP connection is opened and closed in the
    # SAME asyncio task — a module-level singleton gets torn down across tasks by
    # the framework/GC, raising anyio "exit cancel scope in a different task".
    toolbox_endpoint = (os.environ.get("TOOLBOX_ENDPOINT", "") or "").strip()
    if toolbox_endpoint and _host_reachable(toolbox_endpoint):
        print(f"[toolbox] using {toolbox_endpoint}")
    else:
        if toolbox_endpoint:
            print(f"[toolbox] {toolbox_endpoint} unreachable — running without tools")
        else:
            print("[toolbox] TOOLBOX_ENDPOINT not set — running without tools")
        toolbox_endpoint = ""

    # --- Skills from local directory ---
    skills_provider = SkillsProvider(
        skill_paths=str(Path(__file__).parent / "skills")
    )

    # Reusable model client; the agent (with per-request tools) is built per request.
    chat_client = AzureOpenAIResponsesClient(
        project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        deployment_name=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
        credential=_CREDENTIAL,
    )

    _INSTRUCTIONS = (
        "You are a Coverage Assessment Agent for prior authorization requests. "
        "Use your coverage-assessment skill to verify provider credentials, search "
        "coverage policies, and map clinical evidence to policy criteria with "
        "MET/NOT_MET/INSUFFICIENT assessment and per-criterion confidence scoring. "
        "The toolbox exposes the NPI and CMS-coverage tools named '<server>___<tool>' "
        "(e.g. npi___npi_lookup, cms_coverage___search_national_coverage, "
        "cms_coverage___search_local_coverage)."
    )

    # --- Serve as HTTP endpoint for Foundry hosting ---
    # agentserver-core 2.x puts the Responses protocol in ResponsesAgentServerHost.
    app = ResponsesAgentServerHost()

    @app.response_handler
    async def handle_response(
        request: CreateResponse,
        context: ResponseContext,
        cancellation_signal,
    ):
        input_text = await _extract_input_text(request, context)
        try:
            # Connect the toolbox tool inside THIS request task and close it here.
            async with contextlib.AsyncExitStack() as stack:
                tools = []
                if toolbox_endpoint:
                    toolbox = _ToolboxMCPTool(
                        name="coverage-tools",
                        description=(
                            "Foundry toolbox proxying CMS NPI registry and "
                            "Medicare NCD/LCD coverage MCP tools"
                        ),
                        url=toolbox_endpoint,
                        http_client=_TOOLBOX_HTTP_CLIENT,
                        load_prompts=False,
                    )
                    await stack.enter_async_context(toolbox)
                    tools.append(toolbox)
                # default_options enforces CoverageResult schema (token-level JSON).
                agent = chat_client.as_agent(
                    name="coverage-assessment-agent",
                    id="coverage-assessment-agent",  # match registered name for Foundry Traces
                    instructions=_INSTRUCTIONS,
                    tools=tools,
                    context_providers=[skills_provider],
                    default_options={"response_format": CoverageResult},
                )
                output_text = await _run_agent_for_responses(agent, input_text)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — incl. anyio BaseExceptionGroup; never 500 to Foundry
            logger.exception("Coverage agent run failed; returning degraded fallback")
            output_text = _degraded_coverage_result(str(exc))
        return TextResponse(context, request, text=output_text)

    app.run()


if __name__ == "__main__":
    main()
