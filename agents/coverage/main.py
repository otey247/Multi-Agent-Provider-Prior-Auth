"""Coverage Assessment Hosted Agent — server-side MCP via the Foundry Responses API.

Hosted Foundry agents can reach the Foundry project domain but NOT arbitrary
public internet, and an in-container MCP client (agent_framework
MCPStreamableHTTPTool) crashes the request in the hosted runtime (empty-body 500).
Instead we declare the medical-data MCP servers as **server-side `type: mcp`
tools** on the Responses API: Foundry connects to and executes them from its own
network and returns structured output. The agent container opens no MCP
connection of its own — it only calls the Responses API (which the agent's
managed identity is already authorized for, exactly like compliance/synthesis).

Deployed as a Foundry Hosted Agent via azure.ai.agentserver.
Structured output is enforced with openai responses.parse(text_format=CoverageResult).
"""
import asyncio
import json
import logging
import os
from pathlib import Path

from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponsesAgentServerHost,
    TextResponse,
)
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv
from openai import AsyncOpenAI

from schemas import CoverageResult

load_dotenv(override=True)  # override=True required for Foundry-deployed env vars

logger = logging.getLogger(__name__)

# Scope for the Foundry Responses API bearer token (override via env if the
# managed identity is provisioned for a different audience).
_TOKEN_SCOPE = os.environ.get("FOUNDRY_TOKEN_SCOPE", "https://ai.azure.com/.default")
# Managed identity in the hosted runtime; az CLI credential locally.
_CREDENTIAL = DefaultAzureCredential()

_BASE_INSTRUCTIONS = (
    "You are a Coverage Assessment Agent for prior authorization requests. "
    "Follow the coverage-assessment skill instructions below exactly. Use the MCP "
    "tools to verify the provider NPI and search Medicare NCD/LCD coverage "
    "policies, then map clinical evidence to policy criteria with "
    "MET/NOT_MET/INSUFFICIENT assessment and per-criterion confidence, and return "
    "the structured CoverageResult. Available MCP servers (call their tools "
    "directly, e.g. npi_validate, npi_lookup, search_national_coverage, "
    "search_local_coverage, get_coverage_document, get_contractors): 'npi', "
    "'cms_coverage'. Never fabricate tool results."
)


def _load_skill() -> str:
    """Inline the coverage-assessment SKILL.md into the system prompt.

    Replaces agent_framework's SkillsProvider/load_skill mechanism (which we no
    longer use). The skill body is the source of truth for the workflow.
    """
    try:
        return (
            Path(__file__).parent / "skills" / "coverage-assessment" / "SKILL.md"
        ).read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read coverage-assessment SKILL.md: %s", exc)
        return ""


def _mcp_tools() -> list[dict]:
    """Server-side MCP tool specs the Foundry Responses API executes for us."""
    specs = [
        ("npi", os.environ.get("MCP_NPI_REGISTRY", "")),
        ("cms_coverage", os.environ.get("MCP_CMS_COVERAGE", "")),
    ]
    tools: list[dict] = []
    for label, url in specs:
        if url:
            tools.append(
                {
                    "type": "mcp",
                    "server_label": label,
                    "server_url": url,
                    "require_approval": "never",
                }
            )
        else:
            print(f"[mcp] {label} URL not set — skipping")
    print(f"[mcp] {len(tools)} server-side MCP tool(s) attached")
    return tools


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


def _result_to_text(resp) -> str:
    """Extract the structured JSON from an openai Responses parse result."""
    parsed = getattr(resp, "output_parsed", None)
    if parsed is not None:
        return parsed.model_dump_json()
    text = getattr(resp, "output_text", None)
    if text:
        return text
    return str(resp)


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

    system_prompt = _BASE_INSTRUCTIONS + "\n\n# Skill: coverage-assessment\n\n" + _load_skill()
    mcp_tools = _mcp_tools()
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]
    base_url = os.environ["AZURE_AI_PROJECT_ENDPOINT"].rstrip("/") + "/openai/v1"

    # --- Serve as HTTP endpoint for Foundry hosting ---
    app = ResponsesAgentServerHost()

    @app.response_handler
    async def handle_response(
        request: CreateResponse,
        context: ResponseContext,
        cancellation_signal,
    ):
        input_text = await _extract_input_text(request, context)
        try:
            token = (await _CREDENTIAL.get_token(_TOKEN_SCOPE)).token
            async with AsyncOpenAI(base_url=base_url, api_key=token) as client:
                resp = await client.responses.parse(
                    model=deployment,
                    input=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": input_text},
                    ],
                    tools=mcp_tools,
                    text_format=CoverageResult,
                )
            output_text = _result_to_text(resp)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — never 500 to Foundry
            logger.exception("Coverage agent run failed; returning degraded fallback")
            output_text = _degraded_coverage_result(str(exc))
        return TextResponse(context, request, text=output_text)

    app.run()


if __name__ == "__main__":
    main()
