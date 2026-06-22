"""Foundry-native observability endpoints for the in-app Debug Console.

- GET /api/observability/logs/{agent_name}/{session_id}  → SSE proxy of the
  hosted-agent session logstream.
- GET /api/observability/traces/{correlation_id}         → App Insights OTel spans.
- GET /api/observability/links/{correlation_id}          → Foundry/App Insights deep-links.

All degrade gracefully (200 with available=false + reason) when the Foundry /
App Insights environment or RBAC is absent, so local/docker mode is unaffected.
"""
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.foundry_observability import (
    build_links,
    query_run_spans,
    stream_session_logs,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/logs/{agent_name}/{session_id}")
async def get_session_logs(agent_name: str, session_id: str):
    """Stream a hosted agent's per-session container logs as SSE."""
    return StreamingResponse(
        stream_session_logs(agent_name, session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/traces/{correlation_id}")
async def get_run_traces(correlation_id: str):
    """Return the run's OpenTelemetry spans from Application Insights."""
    return await query_run_spans(correlation_id)


@router.get("/links/{correlation_id}")
async def get_links(correlation_id: str = ""):
    """Return Foundry portal + Application Insights deep-links for the run."""
    return build_links(correlation_id)
