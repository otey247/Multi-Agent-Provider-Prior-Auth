"""Clinical Reviewer Hosted Agent — MAF entry point.

Validates ICD-10 codes, extracts clinical indicators with confidence
scoring, searches PubMed literature and ClinicalTrials.gov, and returns
a structured clinical profile for downstream coverage assessment.

Deployed as a Foundry Hosted Agent via azure.ai.agentserver.
MCP tools are wired via MCPStreamableHTTPTool in this container, with Foundry
MCPTool connections registered for proxy routing (see scripts/register_agents.py).
Structured output enforced via default_options={"response_format": ClinicalResult},
which is passed through to every agent.run() call.
"""
import inspect
import json
import logging
import os
from pathlib import Path
from typing import Any

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
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from mcp.shared.exceptions import McpError

from schemas import ClinicalResult

load_dotenv(override=True)  # override=True required for Foundry-deployed env vars

logger = logging.getLogger(__name__)


# DeepSense CloudFront routes on User-Agent — without this header the server
# returns a 301 redirect to the docs site instead of handling MCP messages.
_MCP_HTTP_CLIENT = httpx.AsyncClient(
    headers={"User-Agent": "claude-code/1.0"},
    timeout=httpx.Timeout(60.0),
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


class _SafeMCPTool(MCPStreamableHTTPTool):
    """MCP tool wrapper that returns warnings instead of raising runtime 500s."""

    async def call_tool(self, tool_name: str, **kwargs) -> str:
        try:
            return await super().call_tool(tool_name, **kwargs)
        except Exception as exc:
            logger.exception("MCP tool %s.%s failed", self.name, tool_name)
            return _mcp_warning(self.name, tool_name, exc)


class _ReconnectingMCPTool(MCPStreamableHTTPTool):
    """MCPStreamableHTTPTool that auto-reconnects on expired MCP sessions.

    PubMed's MCP server (pubmed.mcp.claude.com) terminates idle sessions
    after ~10 minutes. The base class retries on ClosedResourceError (TCP
    disconnect) but not on McpError('Session terminated') (MCP-level session
    expiry). This subclass catches both and reconnects once.
    """

    async def call_tool(self, tool_name: str, **kwargs) -> str:
        try:
            return await super().call_tool(tool_name, **kwargs)
        except ToolExecutionException as exc:
            if exc.__cause__ and isinstance(exc.__cause__, McpError) and "Session terminated" in str(exc.__cause__):
                logger.info(
                    "MCP session expired for %s. Reconnecting...", self.name
                )
                try:
                    await self.connect(reset=True)
                    return await super().call_tool(tool_name, **kwargs)
                except Exception as retry_exc:
                    logger.exception(
                        "MCP tool %s.%s failed after reconnect",
                        self.name,
                        tool_name,
                    )
                    return _mcp_warning(self.name, tool_name, retry_exc)

            logger.exception("MCP tool %s.%s failed", self.name, tool_name)
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
    # --- Observability: env var setup for Foundry agentserver ---
    _ai_conn = os.environ.get("APPLICATION_INSIGHTS_CONNECTION_STRING")
    if _ai_conn:
        # Agentserver reads APPLICATIONINSIGHTS_CONNECTION_STRING (no underscore).
        os.environ.setdefault("APPLICATIONINSIGHTS_CONNECTION_STRING", _ai_conn)
        print("[observability] App Insights connection string set for agent-clinical")
    else:
        print("[observability] APPLICATION_INSIGHTS_CONNECTION_STRING not set — telemetry disabled")
    os.environ.setdefault("OTEL_SERVICE_NAME", "agent-clinical")

    # --- MCP tool connections ---
    # MCPStreamableHTTPTool wires tools into the agent container. Foundry also
    # has MCPTool connections registered (see register_agents.py) for proxy routing.
    icd10_tool = _SafeMCPTool(
        name="icd10-codes",
        description="Validate and look up ICD-10 diagnosis and procedure codes",
        url=os.environ["MCP_ICD10_CODES"],
        http_client=_MCP_HTTP_CLIENT,
        load_prompts=False,
    )
    pubmed_tool = _ReconnectingMCPTool(
        name="pubmed",
        description="Search biomedical literature on PubMed",
        url=os.environ["MCP_PUBMED"],
        http_client=_MCP_HTTP_CLIENT,
        load_prompts=False,
    )
    trials_tool = _SafeMCPTool(
        name="clinical-trials",
        description="Search ClinicalTrials.gov for relevant trials",
        url=os.environ["MCP_CLINICAL_TRIALS"],
        http_client=_MCP_HTTP_CLIENT,
        load_prompts=False,
    )

    # --- Skills from local directory ---
    skills_provider = SkillsProvider(
        skill_paths=str(Path(__file__).parent / "skills")
    )

    # --- Agent using Responses API on Microsoft Foundry ---
    # default_options enforces ClinicalResult schema on every agent.run() call
    # made by the Responses protocol handler — token-level JSON constraint, no fence parsing.
    agent = AzureOpenAIResponsesClient(
        project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        deployment_name=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
        credential=DefaultAzureCredential(),
    ).as_agent(
        name="clinical-reviewer-agent",
        id="clinical-reviewer-agent",  # Must match registered agent name for Foundry Traces correlation
        instructions=(
            "You are a Clinical Reviewer Agent for prior authorization requests. "
            "Use your clinical-review skill to validate ICD-10 codes, extract clinical "
            "indicators with confidence scoring, search supporting literature, and "
            "check for relevant clinical trials."
        ),
        tools=[icd10_tool, pubmed_tool, trials_tool],
        context_providers=[skills_provider],
        default_options={"response_format": ClinicalResult},
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
        output_text = await _run_agent_for_responses(agent, input_text)
        return TextResponse(context, request, text=output_text)

    app.run()


if __name__ == "__main__":
    main()
