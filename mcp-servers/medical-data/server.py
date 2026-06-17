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

import asyncio
import contextlib
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

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
# CMS coverage — official CMS Coverage API (api.coverage.cms.gov, MCIM v1.x)
# Real NCD/LCD/Article data: ICD-10 covered/non-covered + HCPCS per policy.
# A free license token (AMA/ADA/AHA click-through) is required for the
# CPT/HCPCS-bearing endpoints; NCD reports are public.
# ---------------------------------------------------------------------------
cms = FastMCP("cms-coverage", stateless_http=True, json_response=True)
_CMS = "https://api.coverage.cms.gov/v1"
_MCD_VIEW = "https://www.cms.gov/medicare-coverage-database/view"
_MCD_SEARCH = "https://www.cms.gov/medicare-coverage-database/search.aspx"

# Small in-process caches (token, state map, NCD list) + a lock to fill them once.
_cms_cache: dict[str, Any] = {"token": None, "states": None, "ncds": None}
_cms_lock = asyncio.Lock()

_STOPWORDS = {
    "and", "or", "the", "for", "of", "to", "in", "a", "an", "billing", "coding",
    "use", "home", "therapy", "treatment", "services", "service", "with",
}
_US_STATE_ABBR = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}


def _terms(text: str) -> set[str]:
    """Meaningful lowercase tokens (drop stopwords and <3-char tokens)."""
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) >= 3 and t not in _STOPWORDS}


def _norm_code(code: str) -> str:
    return (code or "").replace(".", "").strip().upper()


async def _cms_get(path: str, params: dict | None = None, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = await _http.get(f"{_CMS}{path}", params=params or {}, headers=headers)
    resp.raise_for_status()
    return resp.json()


async def _cms_token() -> str:
    if _cms_cache["token"]:
        return _cms_cache["token"]
    async with _cms_lock:
        if not _cms_cache["token"]:
            data = await _cms_get("/metadata/license-agreement/", {"accept": "true"})
            _cms_cache["token"] = data["data"][0]["Token"]
    return _cms_cache["token"]


async def _resolve_state_id(state: str) -> int | None:
    if not state:
        return None
    if _cms_cache["states"] is None:
        data = await _cms_get("/metadata/states/")
        _cms_cache["states"] = {r["description"].lower(): r["state_id"] for r in data.get("data", [])}
    name = state.strip()
    if len(name) == 2:
        name = _US_STATE_ABBR.get(name.upper(), name)
    return _cms_cache["states"].get(name.lower())


async def _collect(path: str, params: dict, token: str, field: str, max_pages: int = 4) -> set[str]:
    """Page through a code endpoint and collect normalized values of `field`."""
    out: set[str] = set()
    next_token = ""
    for _ in range(max_pages):
        page = dict(params, page_size=500)
        if next_token:
            page["next_token"] = next_token
        data = await _cms_get(path, page, token)
        for row in data.get("data", []):
            if row.get(field):
                out.add(_norm_code(str(row[field])))
        next_token = (data.get("meta", {}) or {}).get("next_token") or ""
        if not next_token:
            break
    return out


def _rank(items: list[dict], query: set[str], top: int) -> list[dict]:
    scored = []
    for it in items:
        score = len(query & _terms(it.get("title", "")))
        if score:
            scored.append((score, it))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [it for _, it in scored[:top]]


def _ncd_view_url(api_url: str) -> str:
    qs = parse_qs(urlparse(api_url or "").query)
    nid = (qs.get("ncdid") or [""])[0]
    nver = (qs.get("ncdver") or [""])[0]
    return f"{_MCD_VIEW}/ncd.aspx?ncdid={nid}&ncdver={nver}" if nid else ""


async def _assess_article(art: dict, token: str, dx: list[str], px: list[str]) -> dict:
    """Pull an article's code lists and check the request's dx/px against them."""
    aid = art["document_id"]
    covered, noncovered, hcpcs = await asyncio.gather(
        _collect("/data/article/icd10-covered", {"articleid": aid}, token, "icd10_code_id"),
        _collect("/data/article/icd10-noncovered", {"articleid": aid}, token, "icd10_code_id"),
        _collect("/data/article/hcpc-code", {"articleid": aid}, token, "hcpc_code_id"),
    )
    dx_det = []
    for code in dx:
        n = _norm_code(code)
        status = "covered" if n in covered else "not_covered" if n in noncovered else "not_listed"
        dx_det.append({"code": code, "status": status})
    px_det = [
        {"code": code, "status": "addressed" if _norm_code(code) in hcpcs else "not_addressed"}
        for code in px
    ]
    return {
        "article_id": art.get("document_display_id", str(aid)),
        "title": art.get("title", ""),
        "url": f"{_MCD_VIEW}/article.aspx?articleId={aid}",
        "diagnosis_determinations": dx_det,
        "procedure_determinations": px_det,
        "applies_to_procedure": any(d["status"] == "addressed" for d in px_det),
    }


async def _assess_lcd(lcd: dict, token: str, px: list[str]) -> dict:
    """Pull an LCD's HCPCS list and check the request's procedure codes.

    LCDs (esp. DME, e.g. Oxygen L33797) carry the HCPCS list; the ICD-10
    medical-necessity lists live on the companion billing/coding article.
    """
    lid = lcd["document_id"]
    hcpcs = await _collect("/data/lcd/hcpc-code", {"lcdid": lid}, token, "hcpc_code_id")
    px_det = [
        {"code": code, "status": "addressed" if _norm_code(code) in hcpcs else "not_addressed"}
        for code in px
    ]
    return {
        "lcd_id": lcd.get("document_display_id", str(lid)),
        "title": lcd.get("title", ""),
        "url": f"{_MCD_VIEW}/lcd.aspx?lcdid={lid}",
        "procedure_determinations": px_det,
        "applies_to_procedure": any(d["status"] == "addressed" for d in px_det),
    }


@cms.tool()
async def search_coverage(
    keywords: str,
    procedure_codes: list[str] | None = None,
    diagnosis_codes: list[str] | None = None,
    state: str = "",
) -> dict[str, Any]:
    """Look up Medicare coverage (NCD/LCD) for a service via the CMS Coverage API.

    Returns matching National Coverage Determinations and, when a US state is
    given, state-specific Local Coverage billing/coding articles — including
    whether the supplied diagnosis codes appear in each article's medical-
    necessity covered / non-covered ICD-10 lists and whether the procedure
    codes are addressed. This yields a real MET/NOT_MET/INSUFFICIENT signal
    grounded in CMS data (no fabricated policy text). Falls back to a Medicare
    Coverage Database search link with a manual_review flag when nothing matches.

    Args:
        keywords: Clinical service description (e.g. "home oxygen", "MIGS").
        procedure_codes: HCPCS/CPT codes from the request (e.g. ["E1390"]).
        diagnosis_codes: ICD-10 codes from the request (e.g. ["J44.9"]).
        state: 2-letter or full US state for Local Coverage (e.g. "TX").
    """
    px = procedure_codes or []
    dx = diagnosis_codes or []
    query = _terms(keywords) | {_norm_code(c).lower() for c in px}
    fallback = f"{_MCD_SEARCH}?keyword={keywords.replace(' ', '+')}"

    try:
        token = await _cms_token()

        # NCDs — national, public. Cache the full list once.
        if _cms_cache["ncds"] is None:
            _cms_cache["ncds"] = (await _cms_get("/reports/national-coverage-ncd/")).get("data", [])
        ncds = [
            {
                "ncd_id": n.get("document_display_id"),
                "title": n.get("title"),
                "url": _ncd_view_url(n.get("url", "")),
            }
            for n in _rank(_cms_cache["ncds"], query, top=3)
        ]

        # Local Coverage — state-specific, licensed. Billing/coding ARTICLES carry
        # the ICD-10 medical-necessity lists; LCDs (esp. DME) carry the HCPCS list.
        articles: list[dict] = []
        lcds: list[dict] = []
        state_id = await _resolve_state_id(state)
        if state_id and query:
            art_report, lcd_report = await asyncio.gather(
                _cms_get("/reports/local-coverage-articles/", {"state_id": state_id}, token),
                _cms_get("/reports/local-coverage-final-lcds/", {"state_id": state_id}, token),
            )
            art_cands = _rank(art_report.get("data", []), query, top=3)
            lcd_cands = _rank(lcd_report.get("data", []), query, top=3)
            if art_cands:
                articles = list(
                    await asyncio.gather(*(_assess_article(a, token, dx, px) for a in art_cands))
                )
            if lcd_cands:
                lcds = list(
                    await asyncio.gather(*(_assess_lcd(lcd_, token, px) for lcd_ in lcd_cands))
                )

        matched = bool(ncds or articles or lcds)
        return {
            "status": "matched" if matched else "manual_review",
            "keywords": keywords,
            "state": state,
            "state_resolved": bool(state_id) if state else None,
            "ncds": ncds,
            "local_coverage_articles": articles,
            "local_coverage_lcds": lcds,
            "mcd_search_url": fallback,
            "note": (
                "Coverage grounded in the CMS Coverage API. Diagnosis statuses: "
                "'covered'=supports medical necessity, 'not_covered'=explicitly "
                "excluded, 'not_listed'=not enumerated (treat as INSUFFICIENT). "
                "Provide patient state for Local Coverage article matching."
                if matched
                else "No NCD/LCD match; verify manually via the MCD search link "
                "and treat criteria as INSUFFICIENT."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "manual_review",
            "keywords": keywords,
            "error": f"{type(exc).__name__}: {exc}",
            "mcd_search_url": fallback,
            "note": "CMS Coverage API unavailable; verify manually via the MCD search link.",
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
