"""Consume a Foundry Toolbox MCP endpoint from a hosted agent.

WHY this exists (replaces the old server-side `type: mcp` approach):
Hosted Foundry agents reach the Foundry *project domain* but NOT arbitrary
public internet. The previous design passed the medical-data MCP servers to the
Responses API as server-side `type: mcp` tools (`responses.parse(tools=[{...}])`),
which made the Foundry Responses backend perform the MCP handshake against the
public server URL. When that URL was unreachable (the retired
`mcp.deepsense.ai`, which now 301-redirects to a marketing page), the handshake
stalled and the hosted runtime returned an empty-body HTTP 500 that the handler's
`except` could not catch. A Foundry **Toolbox** is a managed MCP endpoint *on the
project domain* (reachable by the agent) that proxies out to the real MCP servers
from Foundry's own network. So the agent consumes the toolbox as an MCP **client**
— the Toolbox endpoint cannot be passed as a Responses `server_url` (see the
agent-mcp-toolbox skill: "Cannot Use Toolbox as server_url in Responses API").

Lifecycle: the MCP `ClientSession` is opened and closed inside a single request
handler coroutine via `AsyncExitStack`. Creating/tearing it down in the same task
structurally avoids the anyio "exit cancel scope in a different task"
BaseExceptionGroup that bit the old module-level-singleton MCP client.

Structured output is preserved: the model is driven with
`openai responses.parse(text_format=...)` in a tool-calling loop. gpt-5.x are
reasoning models, so the loop uses `previous_response_id` (server-stored state)
rather than re-sending the conversation — this keeps reasoning items intact
across turns, which a stateless input list would drop and error on.
"""
from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)

# Required on every Toolbox MCP request during the V1 preview.
_TOOLBOX_PREVIEW_HEADER = {"Foundry-Features": "Toolboxes=V1Preview"}


def _toolbox_to_openai_tools(mcp_tools) -> list[dict]:
    """Map MCP tool defs (server_label___tool_name) to Responses function tools."""
    specs: list[dict] = []
    for tool in mcp_tools:
        schema = getattr(tool, "inputSchema", None) or {
            "type": "object",
            "properties": {},
        }
        specs.append(
            {
                "type": "function",
                "name": tool.name,
                "description": (getattr(tool, "description", "") or "")[:1024],
                "parameters": schema,
            }
        )
    return specs


def _tool_result_text(result) -> str:
    """Flatten an MCP call_tool result into a string for the model."""
    blocks = getattr(result, "content", None) or []
    parts: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(str(getattr(block, "data", block)))
    out = "\n".join(p for p in parts if p) or "{}"
    if getattr(result, "isError", False):
        return f'{{"error": {json.dumps(out[:1000])}}}'
    return out


async def _open_toolbox_session(
    stack: AsyncExitStack, toolbox_url: str, token: str
) -> ClientSession:
    """Open an initialized MCP client session to the Toolbox endpoint."""
    headers = {"Authorization": f"Bearer {token}", **_TOOLBOX_PREVIEW_HEADER}
    read, write, _ = await stack.enter_async_context(
        streamablehttp_client(toolbox_url, headers=headers)
    )
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session


async def run_with_toolbox(
    *,
    client,
    toolbox_url: str,
    token: str,
    model: str,
    instructions: str,
    input_text: str,
    text_format,
    max_iters: int = 8,
):
    """Run a structured-output agent turn backed by Foundry Toolbox tools.

    Opens a per-request MCP client to ``toolbox_url``, exposes the toolbox's
    tools to the model, runs a tool-calling loop, and returns the openai
    Responses parse result (``.output_parsed`` holds the ``text_format`` model).

    If ``toolbox_url`` is empty the model runs with no tools (still structured).
    Raises on failure — the caller is expected to wrap this and degrade to a
    schema-valid HTTP 200 fallback so the hosted runtime never sees a 500.
    """
    async with AsyncExitStack() as stack:
        tool_specs: list[dict] = []
        session: ClientSession | None = None
        if toolbox_url:
            session = await _open_toolbox_session(stack, toolbox_url, token)
            listed = await session.list_tools()
            tool_specs = _toolbox_to_openai_tools(listed.tools)
            print(f"[toolbox] {len(tool_specs)} tool(s) discovered from {toolbox_url}")
        else:
            print("[toolbox] TOOLBOX_ENDPOINT not set — running without tools")

        parse_kwargs = {"text_format": text_format}
        if tool_specs:
            parse_kwargs["tools"] = tool_specs

        resp = await client.responses.parse(
            model=model,
            instructions=instructions,
            input=[{"role": "user", "content": input_text}],
            **parse_kwargs,
        )

        for _ in range(max_iters):
            calls = [o for o in resp.output if getattr(o, "type", None) == "function_call"]
            if not calls or session is None:
                return resp

            tool_outputs: list[dict] = []
            for call in calls:
                try:
                    args = json.loads(call.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                try:
                    result = await session.call_tool(call.name, args)
                    output = _tool_result_text(result)
                except Exception as exc:  # noqa: BLE001 — surface tool error to model
                    logger.warning("Toolbox tool %s failed: %s", call.name, exc)
                    output = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": output,
                    }
                )

            # Reasoning-safe continuation: server keeps prior turn + reasoning.
            resp = await client.responses.parse(
                model=model,
                input=tool_outputs,
                previous_response_id=resp.id,
                **parse_kwargs,
            )

        # Tool budget exhausted — force a final structured answer without tools.
        logger.warning("Toolbox loop hit max_iters=%s; forcing final answer", max_iters)
        return await client.responses.parse(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": (
                        "Stop calling tools. Return your final structured result "
                        "now using the information gathered so far."
                    ),
                }
            ],
            previous_response_id=resp.id,
            text_format=text_format,
        )
