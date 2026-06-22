"""Foundry-native observability for the in-app Debug Console.

Three capabilities, each best-effort and degrading gracefully when the
environment / RBAC isn't present (so docker-compose and local dev still work):

1. Hosted-agent SESSION LOGSTREAM — proxy the per-session container log stream
   `{project}/agents/{name}/sessions/{sessionId}:logstream` (microsoft-foundry
   troubleshoot sub-skill) and re-emit as SSE.
2. Application Insights TRACES — query the run's OpenTelemetry `gen_ai.*` spans
   via the Azure Monitor Query SDK (trace sub-skill KQL).
3. DEEP LINKS — Foundry portal Traces + Azure portal App Insights blade.

Auth uses the backend managed identity (DefaultAzureCredential): the ai.azure.com
token for the logstream, and Monitoring/Log Analytics Reader for KQL.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import AsyncIterator
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_AI_SCOPE = "https://ai.azure.com/.default"
_LOGSTREAM_API = "2025-11-15-preview"


async def _ai_token() -> str:
    from azure.identity import DefaultAzureCredential
    token = await asyncio.to_thread(
        DefaultAzureCredential().get_token, _AI_SCOPE
    )
    return token.token


async def stream_session_logs(agent_name: str, session_id: str) -> AsyncIterator[str]:
    """Yield SSE frames proxied from the hosted-agent session logstream.

    Yields ready-to-send SSE strings (``event: log\\ndata: {...}\\n\\n``). Emits a
    single ``event: error`` frame if the project endpoint is unset or the
    upstream call fails (e.g. 404 before the sandbox exists)."""
    project = settings.foundry_project_endpoint
    if not project or not session_id:
        yield 'event: error\ndata: {"detail": "logstream unavailable: missing project endpoint or session id"}\n\n'
        return
    url = (
        f"{project}/agents/{agent_name}/sessions/{session_id}:logstream"
        f"?api-version={_LOGSTREAM_API}"
    )
    try:
        token = await _ai_token()
    except Exception as exc:  # noqa: BLE001
        yield f'event: error\ndata: {{"detail": "auth failed: {exc}"}}\n\n'
        return
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
        "Foundry-Features": "HostedAgents=V1Preview",
    }
    try:
        timeout = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")[:500]
                    detail = "sandbox not started yet" if resp.status_code == 404 else body
                    yield f'event: error\ndata: {{"detail": "HTTP {resp.status_code}: {detail}"}}\n\n'
                    return
                async for line in resp.aiter_lines():
                    # Pass the upstream SSE through unchanged; re-frame bare data.
                    if line.startswith("event:") or line.startswith("data:"):
                        yield line + "\n"
                    elif line == "":
                        yield "\n"
    except Exception as exc:  # noqa: BLE001
        yield f'event: error\ndata: {{"detail": "logstream error: {exc}"}}\n\n'


# Spans for one run, keyed by trace/response/conversation id. Mirrors the trace
# sub-skill KQL: filter dependencies/requests on gen_ai.* and the correlation id.
_SPANS_KQL = """
union requests, dependencies
| where timestamp > ago(1d)
| where operation_Id == "{cid}"
    or tostring(customDimensions["gen_ai.response.id"]) == "{cid}"
    or tostring(customDimensions["gen_ai.conversation.id"]) == "{cid}"
| extend
    operation = tostring(customDimensions["gen_ai.operation.name"]),
    gen_model = tostring(customDimensions["gen_ai.request.model"]),
    tool = tostring(customDimensions["gen_ai.tool.name"]),
    agent = coalesce(tostring(customDimensions["gen_ai.agent.name"]),
                     tostring(customDimensions["azure.ai.agentserver.agent_name"])),
    in_tok = tostring(customDimensions["gen_ai.usage.input_tokens"]),
    out_tok = tostring(customDimensions["gen_ai.usage.output_tokens"])
| project timestamp, name, operation, gen_model, tool, agent, in_tok, out_tok,
          duration, success, operation_Id, id
| order by timestamp asc
| take 500
"""


async def query_run_spans(correlation_id: str) -> dict:
    """Return App Insights OTel spans for a run via the Azure Monitor Query SDK.

    ``{"available": bool, "reason": str, "spans": [...]}``. Never raises."""
    rid = settings.APPLICATION_INSIGHTS_RESOURCE_ID
    if not rid:
        return {"available": False, "reason": "APPLICATION_INSIGHTS_RESOURCE_ID not set", "spans": []}
    if not correlation_id:
        return {"available": False, "reason": "no correlation id", "spans": []}
    try:
        from azure.identity import DefaultAzureCredential
        from azure.monitor.query import LogsQueryClient, LogsQueryStatus
    except ImportError as exc:
        return {"available": False, "reason": f"azure-monitor-query not installed: {exc}", "spans": []}

    def _run() -> dict:
        client = LogsQueryClient(DefaultAzureCredential())
        kql = _SPANS_KQL.format(cid=correlation_id.replace('"', ""))
        res = client.query_resource(rid, kql, timespan=timedelta(days=1))
        if res.status == LogsQueryStatus.FAILURE:
            return {"available": False, "reason": "query failed", "spans": []}
        tables = res.tables if res.status == LogsQueryStatus.SUCCESS else (res.partial_data or [])
        spans: list[dict] = []
        for table in tables:
            cols = [c for c in table.columns]
            for row in table.rows:
                spans.append({cols[i]: row[i] for i in range(len(cols))})
        return {"available": True, "reason": "", "spans": spans}

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        logger.warning("App Insights span query failed: %s", exc)
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "spans": []}


def build_links(correlation_id: str = "") -> dict:
    """Foundry portal + Azure App Insights deep-links for the run (best-effort)."""
    links: dict[str, str] = {}
    rid = settings.APPLICATION_INSIGHTS_RESOURCE_ID
    if rid:
        # Azure portal → Application Insights → Transaction search (filtered if id known).
        blade = "/microsoft.insights/components" if False else ""  # placeholder noop
        links["app_insights"] = (
            "https://portal.azure.com/#@/resource" + quote(rid, safe="/") + "/searchV1"
        )
    proj = settings.AZURE_AI_PROJECT_ID
    if proj:
        links["foundry_traces"] = (
            "https://ai.azure.com/manage/project?wsid=" + quote(proj, safe="")
        )
    elif settings.foundry_project_endpoint:
        links["foundry_project"] = settings.foundry_project_endpoint
    if correlation_id:
        links["correlation_id"] = correlation_id
    return links
