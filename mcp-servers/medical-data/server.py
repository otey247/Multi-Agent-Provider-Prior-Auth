"""Self-hosted medical-data MCP server (Streamable HTTP).

Replaces the retired DeepSense MCP servers (mcp.deepsense.ai, now NXDOMAIN)
with thin wrappers over official, free, no-auth public APIs:

  /icd10/mcp          NLM Clinical Tables ICD-10-CM   (lookup_icd10, validate_icd10)
  /clinical_trials/mcp ClinicalTrials.gov API v2      (search_clinical_trials)
  /npi/mcp            CMS NPPES NPI Registry API       (lookup_npi, search_npi)
  /cms_coverage/mcp   Medicare Coverage Database (MCD) (search_coverage — pointer)

Each domain is mounted on its own path so the existing agents only need their
MCP_* env URLs repointed here — no agent code changes. PubMed stays on
pubmed.mcp.claude.com (unaffected by the DeepSense outage).

Transport: MCP Streamable HTTP, stateless + JSON responses (no session to
expire), which is exactly what agent_framework's MCPStreamableHTTPTool speaks.

Run locally:   python server.py            (serves on :8080)
Health check:  GET /health  ->  200 {"status": "ok"}
"""
from __future__ import annotations

import contextlib
import os
from typing import Any

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

USER_AGENT = "prior-auth-medical-mcp/1.0 (+https://github.com/otey247)"
_http = httpx.AsyncClient(
    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    timeout=httpx.Timeout(30.0),
    follow_redirects=True,
)


async def _get_json(url: str, params: dict[str, Any]) -> Any:
    """GET a JSON endpoint, raising for HTTP errors (caller wraps)."""
    resp = await _http.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


def _normalize_code(code: str) -> str:
    """ICD-10 code without the dot, uppercased, for robust comparison."""
    return (code or "").replace(".", "").strip().upper()


# ---------------------------------------------------------------------------
# ICD-10-CM — NLM Clinical Tables (https://clinicaltables.nlm.nih.gov)
# ---------------------------------------------------------------------------
icd10 = FastMCP("icd10-codes", stateless_http=True, json_response=True)
_ICD10_URL = "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search"


@icd10.tool()
async def lookup_icd10(query: str, max_results: int = 10) -> dict[str, Any]:
    """Search ICD-10-CM diagnosis codes by code or description.

    Args:
        query: A partial code (e.g. "J44") or clinical term (e.g. "COPD").
        max_results: Maximum number of matches to return (1-50).
    """
    try:
        n = max(1, min(int(max_results), 50))
        data = await _get_json(
            _ICD10_URL,
            {"terms": query, "maxList": n, "sf": "code,name", "df": "code,name"},
        )
        rows = data[3] if isinstance(data, list) and len(data) > 3 else []
        return {
            "query": query,
            "count": data[0] if isinstance(data, list) else 0,
            "results": [{"code": r[0], "description": r[1]} for r in rows],
        }
    except Exception as exc:  # noqa: BLE001
        return {"query": query, "error": f"{type(exc).__name__}: {exc}", "results": []}


@icd10.tool()
async def validate_icd10(code: str) -> dict[str, Any]:
    """Validate a single ICD-10-CM code and report whether it is billable.

    Billable is approximated: a code is billable when it matches exactly and
    has no more-specific child codes (leaf node).
    """
    try:
        target = _normalize_code(code)
        data = await _get_json(
            _ICD10_URL,
            {"terms": code, "maxList": 50, "sf": "code,name", "df": "code,name"},
        )
        rows = data[3] if isinstance(data, list) and len(data) > 3 else []
        exact = next((r for r in rows if _normalize_code(r[0]) == target), None)
        has_children = any(
            _normalize_code(r[0]).startswith(target) and _normalize_code(r[0]) != target
            for r in rows
        )
        return {
            "code": code,
            "valid": exact is not None,
            "description": exact[1] if exact else "",
            "billable": exact is not None and not has_children,
            "hierarchy_note": (
                "Non-billable category header; use a more specific child code."
                if exact is not None and has_children
                else ""
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"code": code, "valid": False, "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Clinical trials — ClinicalTrials.gov API v2 (https://clinicaltrials.gov)
# ---------------------------------------------------------------------------
trials = FastMCP("clinical-trials", stateless_http=True, json_response=True)
_CT_URL = "https://clinicaltrials.gov/api/v2/studies"


@trials.tool()
async def search_clinical_trials(condition: str, max_results: int = 5) -> dict[str, Any]:
    """Search ClinicalTrials.gov for studies matching a condition or term.

    Args:
        condition: Condition or keyword (e.g. "COPD", "home oxygen therapy").
        max_results: Maximum number of studies to return (1-20).
    """
    try:
        n = max(1, min(int(max_results), 20))
        data = await _get_json(
            _CT_URL,
            {
                "query.cond": condition,
                "pageSize": n,
                "fields": "NCTId,BriefTitle,OverallStatus",
            },
        )
        out = []
        for study in data.get("studies", []):
            ps = study.get("protocolSection", {})
            ident = ps.get("identificationModule", {})
            status = ps.get("statusModule", {})
            out.append(
                {
                    "nct_id": ident.get("nctId", ""),
                    "title": ident.get("briefTitle", ""),
                    "status": status.get("overallStatus", ""),
                }
            )
        return {"condition": condition, "count": len(out), "results": out}
    except Exception as exc:  # noqa: BLE001
        return {"condition": condition, "error": f"{type(exc).__name__}: {exc}", "results": []}


# ---------------------------------------------------------------------------
# NPI — CMS NPPES Registry API (https://npiregistry.cms.hhs.gov)
# ---------------------------------------------------------------------------
npi = FastMCP("npi-registry", stateless_http=True, json_response=True)
_NPI_URL = "https://npiregistry.cms.hhs.gov/api/"


def _format_npi_result(r: dict[str, Any]) -> dict[str, Any]:
    basic = r.get("basic", {})
    taxonomies = r.get("taxonomies", [])
    primary = next((t for t in taxonomies if t.get("primary")), taxonomies[0] if taxonomies else {})
    if basic.get("organization_name"):
        name = basic["organization_name"]
    else:
        name = f"{basic.get('first_name', '')} {basic.get('last_name', '')}".strip()
    deactivated = bool(basic.get("deactivation_date")) or basic.get("status") == "D"
    return {
        "npi": str(r.get("number", "")),
        "name": name,
        "credential": basic.get("credential", ""),
        "specialty": primary.get("desc", ""),
        "taxonomy_code": primary.get("code", ""),
        "status": "inactive" if deactivated else "active",
        "enumeration_type": r.get("enumeration_type", ""),
    }


@npi.tool()
async def lookup_npi(npi_number: str) -> dict[str, Any]:
    """Look up a provider by their 10-digit NPI number."""
    try:
        data = await _get_json(_NPI_URL, {"version": "2.1", "number": npi_number})
        results = data.get("results", [])
        if not results:
            return {"npi": npi_number, "status": "not_found", "found": False}
        return {"found": True, **_format_npi_result(results[0])}
    except Exception as exc:  # noqa: BLE001
        return {"npi": npi_number, "found": False, "error": f"{type(exc).__name__}: {exc}"}


@npi.tool()
async def search_npi(
    first_name: str = "",
    last_name: str = "",
    state: str = "",
    taxonomy_description: str = "",
    max_results: int = 10,
) -> dict[str, Any]:
    """Search the NPI registry by provider name, state, and/or specialty."""
    try:
        params: dict[str, Any] = {
            "version": "2.1",
            "limit": max(1, min(int(max_results), 50)),
        }
        if first_name:
            params["first_name"] = first_name
        if last_name:
            params["last_name"] = last_name
        if state:
            params["state"] = state
        if taxonomy_description:
            params["taxonomy_description"] = taxonomy_description
        data = await _get_json(_NPI_URL, params)
        return {
            "count": data.get("result_count", 0),
            "results": [_format_npi_result(r) for r in data.get("results", [])],
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "results": []}


# ---------------------------------------------------------------------------
# CMS coverage — Medicare Coverage Database (no public JSON API; pointer tool)
# ---------------------------------------------------------------------------
cms = FastMCP("cms-coverage", stateless_http=True, json_response=True)
_MCD_SEARCH = "https://www.cms.gov/medicare-coverage-database/search.aspx"


@cms.tool()
async def search_coverage(keywords: str, codes: list[str] | None = None) -> dict[str, Any]:
    """Return Medicare coverage (NCD/LCD) guidance for keywords/codes.

    The Medicare Coverage Database has no public JSON API, so this returns a
    deep link to the official MCD search plus a manual-review flag rather than
    fabricating policy text. The coverage agent should treat coverage criteria
    sourced this way as requiring manual verification.
    """
    code_str = ", ".join(codes) if codes else ""
    query = "+".join(p for p in [keywords, code_str] if p).replace(" ", "+")
    return {
        "status": "manual_review",
        "keywords": keywords,
        "codes": codes or [],
        "mcd_search_url": f"{_MCD_SEARCH}?keyword={query}",
        "note": (
            "No programmatic NCD/LCD API exists. Verify coverage manually via the "
            "linked Medicare Coverage Database search. Treat criteria as INSUFFICIENT "
            "until confirmed."
        ),
    }


# ---------------------------------------------------------------------------
# ASGI app — mount each domain on its own path, share one lifespan
# ---------------------------------------------------------------------------
_SERVERS = {
    "icd10": icd10,
    "clinical_trials": trials,
    "npi": npi,
    "cms_coverage": cms,
}


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "domains": sorted(_SERVERS)})


@contextlib.asynccontextmanager
async def lifespan(_: Starlette):
    # Each FastMCP streamable-HTTP app needs its session manager running.
    async with contextlib.AsyncExitStack() as stack:
        for srv in _SERVERS.values():
            await stack.enter_async_context(srv.session_manager.run())
        try:
            yield
        finally:
            await _http.aclose()


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/readiness", health),
        *[Mount(f"/{path}", app=srv.streamable_http_app()) for path, srv in _SERVERS.items()],
    ],
    lifespan=lifespan,
)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
