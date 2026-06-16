"""Coverage Assessment Hosted Agent — MAF entry point.

Verifies provider NPI, searches Medicare coverage policies via CMS MCP,
maps clinical findings to policy criteria with MET/NOT_MET/INSUFFICIENT
assessment, and returns a structured coverage evaluation.

Deployed as a Foundry Hosted Agent via azure.ai.agentserver.
MCP tools are wired via MCPStreamableHTTPTool in this container, with Foundry
MCPTool connections registered for proxy routing (see scripts/register_agents.py).
Structured output enforced via default_options={"response_format": CoverageResult},
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

from schemas import CoverageResult

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
        except ToolExecutionException as exc:
            logger.exception("MCP tool %s.%s failed", self.name, tool_name)
            return _mcp_warning(self.name, tool_name, exc)
        except Exception as exc:
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
    # --- Observability: env var setup for Foundry agentserver adapter ---
    _ai_conn = os.environ.get("APPLICATION_INSIGHTS_CONNECTION_STRING")
    if _ai_conn:
        os.environ.setdefault("APPLICATIONINSIGHTS_CONNECTION_STRING", _ai_conn)
        print("[observability] App Insights connection string set for agent-coverage")
    else:
        print("[observability] APPLICATION_INSIGHTS_CONNECTION_STRING not set — telemetry disabled")
    os.environ.setdefault("OTEL_SERVICE_NAME", "agent-coverage")

    # --- MCP tool connections ---
    # MCPStreamableHTTPTool wires tools into the agent container. Foundry also
    # has MCPTool connections registered (see register_agents.py) for proxy routing.
    npi_tool = _SafeMCPTool(
        name="npi-registry",
        description="Validate and look up provider NPI numbers from CMS NPPES",
        url=os.environ["MCP_NPI_REGISTRY"],
        http_client=_MCP_HTTP_CLIENT,
        load_prompts=False,
    )
    cms_tool = _SafeMCPTool(
        name="cms-coverage",
        description="Search Medicare NCDs, LCDs and coverage policy documents",
        url=os.environ["MCP_CMS_COVERAGE"],
        http_client=_MCP_HTTP_CLIENT,
        load_prompts=False,
    )

    # --- Skills from local directory ---
    skills_provider = SkillsProvider(
        skill_paths=str(Path(__file__).parent / "skills")
    )

    # --- Agent using Responses API on Microsoft Foundry ---
    # default_options enforces CoverageResult schema on every agent.run() call
    # made by the Responses protocol handler — token-level JSON constraint, no fence parsing.
    agent = AzureOpenAIResponsesClient(
        project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        deployment_name=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
        credential=DefaultAzureCredential(),
    ).as_agent(
        name="coverage-assessment-agent",
        id="coverage-assessment-agent",  # Must match registered agent name for Foundry Traces correlation
        instructions=(
            "You are a Coverage Assessment Agent for prior authorization requests. "
            "Use your coverage-assessment skill to verify provider credentials, search "
            "coverage policies, and map clinical evidence to policy criteria with "
            "MET/NOT_MET/INSUFFICIENT assessment and per-criterion confidence scoring."
        ),
        tools=[npi_tool, cms_tool],
        context_providers=[skills_provider],
        default_options={"response_format": CoverageResult},
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
