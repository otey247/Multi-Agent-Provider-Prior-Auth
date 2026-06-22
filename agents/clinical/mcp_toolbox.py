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
import time
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from redact import redact

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
    phi_values: list[str] | None = None,
    max_iters: int = 8,
):
    """Run a structured-output agent turn backed by Foundry Toolbox tools.

    Opens a per-request MCP client to ``toolbox_url``, exposes the toolbox's
    tools to the model, runs a tool-calling loop, and returns the openai
    Responses parse result (``.output_parsed`` holds the ``text_format`` model).

    If ``toolbox_url`` is empty the model runs with no tools (still structured).
    Raises on failure — the caller is expected to wrap this and degrade to a
    schema-valid HTTP 200 fallback so the hosted runtime never sees a 500.

    Returns ``(response, tool_audit, model_calls)``:
      * ``tool_audit`` — authoritative per-tool record (status, timing, and
        PHI-redacted ``args_full``/``result_full`` + short summaries), injected
        into the structured result's ``tool_results``.
      * ``model_calls`` — one ``{"kind":"llm", model, duration_ms,
        started_offset_ms, input_tokens, output_tokens}`` entry per
        ``responses.parse`` round, for the Debug Console timeline/events.
    ``phi_values`` are request values (patient name/DOB/insurance) masked from
    captured payloads alongside generic PHI patterns.
    """
    tool_audit: list[dict] = []
    model_calls: list[dict] = []
    run_start = time.monotonic()

    async def _parse(**kwargs):
        """responses.parse wrapper that records a timed llm span + token usage."""
        _t0 = time.monotonic()
        _off = int((_t0 - run_start) * 1000)
        r = await client.responses.parse(**kwargs)
        usage = getattr(r, "usage", None)
        model_calls.append({
            "kind": "llm",
            "model": kwargs.get("model", model),
            "order": len(model_calls),
            "duration_ms": int((time.monotonic() - _t0) * 1000),
            "started_offset_ms": _off,
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        })
        return r

    # Manage the MCP session explicitly: a newer mcp client can raise an anyio
    # cancel-scope BaseExceptionGroup on session *teardown*; capturing the result
    # first and closing in a suppressing finally keeps a successful run from being
    # discarded by a teardown-only error (which would otherwise degrade to 200).
    final_resp = None
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
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

        resp = await _parse(
            model=model,
            instructions=instructions,
            input=[{"role": "user", "content": input_text}],
            **parse_kwargs,
        )

        for _ in range(max_iters):
            calls = [o for o in resp.output if getattr(o, "type", None) == "function_call"]
            if not calls or session is None:
                final_resp = resp
                break

            tool_outputs: list[dict] = []
            for call in calls:
                try:
                    args = json.loads(call.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                started_offset_ms = int((time.monotonic() - run_start) * 1000)
                t0 = time.monotonic()
                try:
                    result = await session.call_tool(call.name, args)
                    output = _tool_result_text(result)
                    is_error = bool(getattr(result, "isError", False)) or output.lstrip().startswith('{"error"')
                except Exception as exc:  # noqa: BLE001 — surface tool error to model
                    logger.warning("Toolbox tool %s failed: %s", call.name, exc)
                    output = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
                    is_error = True
                duration_ms = int((time.monotonic() - t0) * 1000)
                # server_label___tool_name -> ("server_label", "tool_name")
                label, _sep, short = (call.name or "").partition("___")
                # Redact PHI before any payload enters the trace.
                args_red = redact(json.dumps(args) if args else "", phi_values)
                out_red = redact(output, phi_values)
                # Authoritative, timed audit of the call we actually executed —
                # surfaced in the in-app Debug Console + tool_results.
                tool_audit.append(
                    {
                        "tool_name": call.name,
                        "server_label": label if short else "",
                        "tool": short or call.name,
                        "order": len(tool_audit),
                        "status": "fail" if is_error else "pass",
                        "duration_ms": duration_ms,
                        "started_offset_ms": started_offset_ms,
                        "args_summary": args_red[:300],
                        "result_summary": out_red[:500],
                        "args_full": args_red[:4000],
                        "result_full": out_red[:8000],
                        "detail": (
                            f"{call.name} failed: {out_red[:300]}"
                            if is_error
                            else f"{call.name} executed successfully."
                        ),
                    }
                )
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": output,
                    }
                )

            # Reasoning-safe continuation: server keeps prior turn + reasoning.
            resp = await _parse(
                model=model,
                input=tool_outputs,
                previous_response_id=resp.id,
                **parse_kwargs,
            )

        else:
            # Tool budget exhausted — force a final structured answer without tools.
            logger.warning("Toolbox loop hit max_iters=%s; forcing final answer", max_iters)
            final_resp = await _parse(
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
    finally:
        # Close the per-request MCP session. Suppress teardown-only errors
        # (anyio cancel-scope BaseExceptionGroup) — the result is already captured.
        try:
            await stack.aclose()
        except BaseException as exc:  # noqa: BLE001
            logger.warning("Toolbox session teardown raised (ignored): %s", type(exc).__name__)
    return final_resp, tool_audit, model_calls
