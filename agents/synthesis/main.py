"""Synthesis Decision Hosted Agent — MAF entry point.

Synthesizes outputs from Compliance, Clinical, and Coverage agents into
a final APPROVE or PEND recommendation using gate-based evaluation,
weighted confidence scoring, and a structured audit trail.

Deployed as a Foundry Hosted Agent via azure.ai.agentserver.
No MCP connections required — synthesis is pure reasoning over agent outputs.
Structured output enforced via default_options={"response_format": SynthesisOutput},
which is passed through to every agent.run() call.
"""
import inspect
import json
import os
from pathlib import Path
from typing import Any

from agent_framework import SkillsProvider
from agent_framework.azure import AzureOpenAIResponsesClient
from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponsesAgentServerHost,
    TextResponse,
)
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from schemas import SynthesisOutput

load_dotenv(override=True)  # override=True required for Foundry-deployed env vars


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
        print("[observability] App Insights connection string set for agent-synthesis")
    else:
        print("[observability] APPLICATION_INSIGHTS_CONNECTION_STRING not set — telemetry disabled")
    os.environ.setdefault("OTEL_SERVICE_NAME", "agent-synthesis")

    # --- No MCP tools — synthesis is pure reasoning over agent outputs ---

    # --- Skills from local directory ---
    skills_provider = SkillsProvider(
        skill_paths=str(Path(__file__).parent / "skills")
    )

    # --- Agent using Responses API on Microsoft Foundry ---
    # default_options enforces SynthesisOutput schema on every agent.run() call
    # made by the Responses protocol handler — token-level JSON constraint, no fence parsing.
    agent = AzureOpenAIResponsesClient(
        project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        deployment_name=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
        credential=DefaultAzureCredential(),
    ).as_agent(
        name="synthesis-agent",
        id="synthesis-agent",  # Must match registered agent name for Foundry Traces correlation
        instructions=(
            "You are the Synthesis Agent for prior authorization review. "
            "Use your synthesis-decision skill to evaluate the outputs from the "
            "Compliance, Clinical Reviewer, and Coverage agents through a strict "
            "3-gate pipeline (Provider → Codes → Medical Necessity) and produce "
            "a single APPROVE or PEND recommendation with weighted confidence scoring "
            "and a complete audit trail."
        ),
        tools=[],
        context_providers=[skills_provider],
        default_options={"response_format": SynthesisOutput},
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
