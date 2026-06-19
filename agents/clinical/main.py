"""Clinical Reviewer Hosted Agent — MCP via the Foundry Toolbox endpoint.

Hosted Foundry agents reach the Foundry project domain but NOT arbitrary public
internet. We consume the medical-data MCP servers through a **Foundry Toolbox**
(`clinical-tools`): a managed MCP endpoint *on the project domain* that proxies
out to the real servers from Foundry's own network. The agent connects to the
toolbox as an MCP **client** (the toolbox cannot be passed to the Responses API
as a `type: mcp` server_url). This replaces the previous server-side `type: mcp`
approach, whose Responses-backend handshake against the public (now retired)
mcp.deepsense.ai URL stalled and returned an uncatchable empty-body HTTP 500.

The MCP session is opened/closed per request (see mcp_toolbox.run_with_toolbox),
which avoids the cross-task anyio teardown bug of a module-level MCP client.

Deployed as a Foundry Hosted Agent via azure.ai.agentserver.
Structured output is enforced with openai responses.parse(text_format=ClinicalResult).
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
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from openai import AsyncOpenAI

from mcp_toolbox import run_with_toolbox
from schemas import ClinicalResult

load_dotenv(override=True)  # override=True required for Foundry-deployed env vars

logger = logging.getLogger(__name__)

# Scope for the Foundry Responses API bearer token (override via env if the
# managed identity is provisioned for a different audience).
_TOKEN_SCOPE = os.environ.get("FOUNDRY_TOKEN_SCOPE", "https://ai.azure.com/.default")
# Managed identity in the hosted runtime; az CLI credential locally.
_CREDENTIAL = DefaultAzureCredential()

_BASE_INSTRUCTIONS = (
    "You are a Clinical Reviewer Agent for prior authorization requests. "
    "Follow the clinical-review skill instructions below exactly. Use the MCP "
    "tools to validate each ICD-10 code, search PubMed literature, and check "
    "ClinicalTrials.gov, then return the structured ClinicalResult. Available MCP "
    "servers (call their tools directly, e.g. validate_code, lookup_code, "
    "get_hierarchy, search_articles, search_trials): 'icd10', 'pubmed', "
    "'clinical_trials'. Never fabricate tool results."
)


def _load_skill() -> str:
    """Inline the clinical-review SKILL.md into the system prompt.

    Replaces agent_framework's SkillsProvider/load_skill mechanism (which we no
    longer use). The skill body is the source of truth for the workflow.
    """
    try:
        return (
            Path(__file__).parent / "skills" / "clinical-review" / "SKILL.md"
        ).read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read clinical-review SKILL.md: %s", exc)
        return ""


def _toolbox_url() -> str:
    """Resolve the clinical-tools Foundry Toolbox MCP endpoint.

    Prefers TOOLBOX_ENDPOINT (injected by scripts/register_agents.py); otherwise
    builds it from the project endpoint + toolbox name. Empty string means run
    without tools (degraded but schema-valid).
    """
    explicit = (os.environ.get("TOOLBOX_ENDPOINT", "") or "").strip()
    if explicit:
        return explicit
    project = (os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "") or "").strip().rstrip("/")
    name = os.environ.get("TOOLBOX_NAME", "clinical-tools")
    if project:
        return f"{project}/toolboxes/{name}/mcp?api-version=v1"
    print("[toolbox] no TOOLBOX_ENDPOINT or project endpoint — tools disabled")
    return ""


def _degraded_clinical_result(detail: str) -> str:
    """Schema-valid ClinicalResult emitted when the agent run cannot complete.

    Keeps the hosted agent returning HTTP 200 with a conservative manual-review
    payload instead of a 500 when the model call or MCP tools fail.
    """
    return json.dumps(
        {
            "agent_name": "Clinical Reviewer Agent",
            "checks_performed": [],
            "diagnosis_validation": [],
            "procedure_validation": [],
            "clinical_extraction": None,
            "literature_support": [],
            "clinical_trials": [],
            "clinical_summary": (
                "Automated clinical review could not complete because one or more "
                "external tools or the model call were unavailable. Route to manual "
                "clinical review."
            ),
            "tool_results": [
                {
                    "tool_name": "clinical-tools",
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
    # --- Observability: env var setup for Foundry agentserver ---
    _ai_conn = os.environ.get("APPLICATION_INSIGHTS_CONNECTION_STRING") or os.environ.get("MONITORING_CONNECTION_STRING")
    if _ai_conn:
        os.environ.setdefault("APPLICATIONINSIGHTS_CONNECTION_STRING", _ai_conn)
        print("[observability] App Insights connection string set for agent-clinical")
    else:
        print("[observability] APPLICATION_INSIGHTS_CONNECTION_STRING not set — telemetry disabled")
    os.environ.setdefault("OTEL_SERVICE_NAME", "agent-clinical")

    system_prompt = _BASE_INSTRUCTIONS + "\n\n# Skill: clinical-review\n\n" + _load_skill()
    toolbox_url = _toolbox_url()
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]
    base_url = os.environ["AZURE_AI_PROJECT_ENDPOINT"].rstrip("/") + "/openai/v1"
    print(f"[toolbox] clinical-tools endpoint: {toolbox_url or '(none — tools disabled)'}")

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
            token = (await asyncio.to_thread(_CREDENTIAL.get_token, _TOKEN_SCOPE)).token
            # timeout: a stalled toolbox/model call raises (caught below) and
            # degrades to HTTP 200 instead of letting the gateway emit a 500.
            async with AsyncOpenAI(base_url=base_url, api_key=token, timeout=120.0) as client:
                resp = await run_with_toolbox(
                    client=client,
                    toolbox_url=toolbox_url,
                    token=token,
                    model=deployment,
                    instructions=system_prompt,
                    input_text=input_text,
                    text_format=ClinicalResult,
                )
            output_text = _result_to_text(resp)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — never 500 to Foundry
            logger.exception("Clinical agent run failed; returning degraded fallback")
            output_text = _degraded_clinical_result(str(exc))
        return TextResponse(context, request, text=output_text)

    app.run()


if __name__ == "__main__":
    main()
