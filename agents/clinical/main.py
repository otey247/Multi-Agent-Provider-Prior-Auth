"""Clinical Reviewer Hosted Agent — server-side MCP via the Foundry Responses API.

Hosted Foundry agents can reach the Foundry project domain but NOT arbitrary
public internet, and an in-container MCP client (agent_framework
MCPStreamableHTTPTool) crashes the request in the hosted runtime (empty-body 500).
Instead we declare the medical-data MCP servers as **server-side `type: mcp`
tools** on the Responses API: Foundry connects to and executes them from its own
network and returns structured output. The agent container opens no MCP
connection of its own — it only calls the Responses API (which the agent's
managed identity is already authorized for, exactly like compliance/synthesis).

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


def _mcp_tools() -> list[dict]:
    """Server-side MCP tool specs the Foundry Responses API executes for us."""
    specs = [
        ("icd10", os.environ.get("MCP_ICD10_CODES", "")),
        ("pubmed", os.environ.get("MCP_PUBMED", "")),
        ("clinical_trials", os.environ.get("MCP_CLINICAL_TRIALS", "")),
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
            token = (await asyncio.to_thread(_CREDENTIAL.get_token, _TOKEN_SCOPE)).token
            async with AsyncOpenAI(base_url=base_url, api_key=token) as client:
                resp = await client.responses.parse(
                    model=deployment,
                    instructions=system_prompt,
                    input=[{"role": "user", "content": input_text}],
                    tools=mcp_tools,
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
