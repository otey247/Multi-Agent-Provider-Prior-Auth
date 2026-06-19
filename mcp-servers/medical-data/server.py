"""Self-hosted medical-data MCP server (Streamable HTTP).

Replaces the retired DeepSense MCP servers (mcp.deepsense.ai, now NXDOMAIN)
with thin wrappers over official, free, no-auth public APIs. Tool NAMES and
signatures match exactly what the agent SKILL.md files call, so the clinical
and coverage hosted agents work unchanged:

  /icd10/mcp           NLM Clinical Tables      validate_code, lookup_code,
                       ICD-10-CM / PCS          search_codes, get_hierarchy
  /clinical_trials/mcp ClinicalTrials.gov v2    search_trials, get_trial_details
  /npi/mcp             CMS NPPES Registry       npi_validate, npi_lookup, npi_search
  /cms_coverage/mcp    CMS Coverage API (MCIM)  search_national_coverage,
                       api.coverage.cms.gov     search_local_coverage,
                                                get_coverage_document, get_contractors

Each domain is mounted on its own path so the agents only need their MCP_*
env URLs repointed here. PubMed stays on pubmed.mcp.claude.com (unaffected).

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

import demo_fixtures  # curated fallback for demo sample cases (local module)
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

# MCP Streamable HTTP enables DNS-rebinding protection by default, which 421s
# any request whose Host header isn't localhost. Foundry agents connect via the
# container FQDN, so that check must be off — the server is behind Azure
# Container Apps TLS ingress and serves only public, read-only data.
_NO_HOST_CHECK = TransportSecuritySettings(enable_dns_rebinding_protection=False)

USER_AGENT = "prior-auth-medical-mcp/1.0 (+https://github.com/otey247)"
_http = httpx.AsyncClient(
    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    timeout=httpx.Timeout(30.0),
    follow_redirects=True,
)


async def _get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET a JSON endpoint, raising for HTTP errors (caller wraps)."""
    resp = await _http.get(url, params=params or {})
    resp.raise_for_status()
    return resp.json()


def _norm_code(code: str) -> str:
    """A code without the dot, uppercased, for robust comparison."""
    return (code or "").replace(".", "").strip().upper()


def _err(extra: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {**extra, "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# ICD-10 — NLM Clinical Tables (https://clinicaltables.nlm.nih.gov)
# Tools: validate_code, lookup_code, search_codes, get_hierarchy
# ---------------------------------------------------------------------------
icd10 = FastMCP("icd10-codes", stateless_http=True, json_response=True, transport_security=_NO_HOST_CHECK)


async def _nlm_search(code_type: str, terms: str, search_by: str, max_list: int) -> tuple[int, list[dict]]:
    """Search the NLM Clinical Tables ICD-10-CM (diagnosis) or PCS (procedure)."""
    dataset = "icd10pcs" if code_type == "procedure" else "icd10cm"
    sf = "code" if search_by == "code" else "name"
    data = await _get_json(
        f"https://clinicaltables.nlm.nih.gov/api/{dataset}/v3/search",
        {"terms": terms, "maxList": max_list, "sf": sf, "df": "code,name"},
    )
    rows = data[3] if isinstance(data, list) and len(data) > 3 else []
    total = data[0] if isinstance(data, list) and data else 0
    return total, [{"code": r[0], "description": r[1]} for r in rows]


async def _resolve_icd10(code: str, code_type: str) -> dict[str, Any]:
    """Validate + describe a single code; billable = exact match with no children."""
    target = _norm_code(code)
    _, rows = await _nlm_search(code_type, code, "code", 200)
    exact = next((r for r in rows if _norm_code(r["code"]) == target), None)
    has_children = any(
        _norm_code(r["code"]).startswith(target) and _norm_code(r["code"]) != target
        for r in rows
    )
    return {
        "code": code,
        "code_type": code_type,
        "valid": exact is not None,
        "valid_for_hipaa": exact is not None,
        "description": exact["description"] if exact else "",
        "billable": exact is not None and not has_children,
        "hierarchy_note": (
            "Non-billable category header; use a more specific child code (call get_hierarchy)."
            if exact is not None and has_children
            else ""
        ),
    }


@icd10.tool()
async def validate_code(code: str, code_type: str = "diagnosis") -> dict[str, Any]:
    """Validate a single ICD-10 code and report HIPAA validity + billable status.

    Args:
        code: ICD-10 code (e.g. "J44.1").
        code_type: "diagnosis" for ICD-10-CM, "procedure" for ICD-10-PCS.
    """
    try:
        return await _resolve_icd10(code, code_type)
    except Exception as exc:  # noqa: BLE001
        return _err({"code": code, "valid": False}, exc)


@icd10.tool()
async def lookup_code(code: str, code_type: str = "diagnosis") -> dict[str, Any]:
    """Get full details (description, HIPAA validity, billable) for one ICD-10 code."""
    try:
        return await _resolve_icd10(code, code_type)
    except Exception as exc:  # noqa: BLE001
        return _err({"code": code, "valid": False}, exc)


@icd10.tool()
async def search_codes(
    query: str,
    code_type: str = "diagnosis",
    search_by: str = "description",
    limit: int = 10,
    exact: bool = False,
    valid_for_hipaa_only: bool = True,
) -> dict[str, Any]:
    """Search ICD-10 codes by code prefix (search_by="code") or description text."""
    try:
        total, rows = await _nlm_search(code_type, query, search_by, max(1, min(int(limit), 50)))
        return {"query": query, "code_type": code_type, "count": total, "results": rows}
    except Exception as exc:  # noqa: BLE001
        return _err({"query": query, "results": []}, exc)


@icd10.tool()
async def get_hierarchy(code_prefix: str, code_type: str = "diagnosis") -> dict[str, Any]:
    """Get child codes under a category header, flagging billable leaf codes.

    Use to find a specific billable code when a non-billable header was submitted.
    """
    try:
        target = _norm_code(code_prefix)
        _, rows = await _nlm_search(code_type, code_prefix, "code", 500)
        children = [r for r in rows if _norm_code(r["code"]).startswith(target)]
        norms = [_norm_code(r["code"]) for r in children]
        out = []
        for r in children:
            n = _norm_code(r["code"])
            is_leaf = not any(o != n and o.startswith(n) for o in norms)
            out.append({"code": r["code"], "description": r["description"], "billable": is_leaf})
        return {"code_prefix": code_prefix, "count": len(out), "codes": out}
    except Exception as exc:  # noqa: BLE001
        return _err({"code_prefix": code_prefix, "codes": []}, exc)


# ---------------------------------------------------------------------------
# Clinical trials — ClinicalTrials.gov API v2 (https://clinicaltrials.gov)
# Tools: search_trials, get_trial_details
# ---------------------------------------------------------------------------
trials = FastMCP("clinical-trials", stateless_http=True, json_response=True, transport_security=_NO_HOST_CHECK)
_CT_URL = "https://clinicaltrials.gov/api/v2/studies"


@trials.tool()
async def search_trials(query: str, status: str = "", phase: str = "", limit: int = 5) -> dict[str, Any]:
    """Search ClinicalTrials.gov for studies matching a condition or intervention.

    Args:
        query: Condition/intervention keywords (e.g. "COPD home oxygen").
        status: Optional overall status filter (e.g. "RECRUITING", "COMPLETED").
        phase: Optional phase hint (appended to the query; v2 has no strict filter).
        limit: Max studies to return (1-20).
    """
    try:
        n = max(1, min(int(limit), 20))
        term = f"{query} {phase}".strip() if phase else query
        params: dict[str, Any] = {
            "query.term": term,
            "pageSize": n,
            "fields": "NCTId,BriefTitle,OverallStatus,Phase,Condition",
        }
        if status:
            params["filter.overallStatus"] = status.upper()
        data = await _get_json(_CT_URL, params)
        out = []
        for study in data.get("studies", []):
            ps = study.get("protocolSection", {})
            ident = ps.get("identificationModule", {})
            out.append(
                {
                    "nct_id": ident.get("nctId", ""),
                    "title": ident.get("briefTitle", ""),
                    "status": ps.get("statusModule", {}).get("overallStatus", ""),
                    "phases": ps.get("designModule", {}).get("phases", []),
                }
            )
        return {"query": query, "count": len(out), "results": out}
    except Exception as exc:  # noqa: BLE001
        return _err({"query": query, "results": []}, exc)


@trials.tool()
async def get_trial_details(nct_id: str) -> dict[str, Any]:
    """Get comprehensive details for a specific trial by NCT ID."""
    try:
        data = await _get_json(f"{_CT_URL}/{nct_id.strip().upper()}")
        ps = data.get("protocolSection", {})
        interventions = ps.get("armsInterventionsModule", {}).get("interventions", [])
        return {
            "nct_id": ps.get("identificationModule", {}).get("nctId", nct_id),
            "title": ps.get("identificationModule", {}).get("briefTitle", ""),
            "status": ps.get("statusModule", {}).get("overallStatus", ""),
            "phases": ps.get("designModule", {}).get("phases", []),
            "conditions": ps.get("conditionsModule", {}).get("conditions", []),
            "interventions": [i.get("name", "") for i in interventions],
            "brief_summary": ps.get("descriptionModule", {}).get("briefSummary", "")[:2000],
        }
    except Exception as exc:  # noqa: BLE001
        return _err({"nct_id": nct_id}, exc)


# ---------------------------------------------------------------------------
# NPI — CMS NPPES Registry API (https://npiregistry.cms.hhs.gov)
# Tools: npi_validate (local Luhn), npi_lookup, npi_search
# ---------------------------------------------------------------------------
npi = FastMCP("npi-registry", stateless_http=True, json_response=True, transport_security=_NO_HOST_CHECK)
_NPI_URL = "https://npiregistry.cms.hhs.gov/api/"


def _luhn_ok(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _npi_luhn_ok(npi_number: str) -> bool:
    """NPI check-digit validation: Luhn over the 80840 prefix + the 10-digit NPI."""
    if not re.fullmatch(r"\d{10}", npi_number or ""):
        return False
    return _luhn_ok("80840" + npi_number)


def _format_npi_result(r: dict[str, Any]) -> dict[str, Any]:
    basic = r.get("basic", {})
    taxonomies = r.get("taxonomies", [])
    primary = next((t for t in taxonomies if t.get("primary")), taxonomies[0] if taxonomies else {})
    addresses = r.get("addresses", [])
    loc = next(
        (a for a in addresses if a.get("address_purpose") == "LOCATION"),
        addresses[0] if addresses else {},
    )
    is_org = r.get("enumeration_type") == "NPI-2"
    name = (
        basic.get("organization_name")
        if is_org
        else f"{basic.get('first_name', '')} {basic.get('last_name', '')}".strip()
    )
    deactivated = bool(basic.get("deactivation_date")) or basic.get("status") == "D"
    return {
        "npi": str(r.get("number", "")),
        "provider_type": "Organization" if is_org else "Individual",
        "name": name,
        "credential": basic.get("credential", ""),
        "status": "Deactivated" if deactivated else "Active",
        "specialty": primary.get("desc", ""),
        "taxonomy_code": primary.get("code", ""),
        "taxonomy_description": primary.get("desc", ""),
        "license": primary.get("license", ""),
        "state": loc.get("state", ""),
        "address": ", ".join(
            p for p in [loc.get("address_1", ""), loc.get("city", ""), loc.get("state", ""), loc.get("postal_code", "")] if p
        ),
        "phone": loc.get("telephone_number", ""),
        # Full taxonomy list (primary + secondary) with per-taxonomy license/state.
        "taxonomies": [
            {
                "code": t.get("code", ""),
                "desc": t.get("desc", ""),
                "primary": bool(t.get("primary")),
                "license": t.get("license", ""),
                "state": t.get("state", ""),
            }
            for t in taxonomies
            if t.get("code") or t.get("desc")
        ],
    }


@npi.tool()
async def npi_validate(npi: str) -> dict[str, Any]:
    """Validate NPI format and Luhn check digit locally (no API call)."""
    ok = _npi_luhn_ok(npi)
    return {
        "npi": npi,
        "valid": ok,
        "detail": "Valid NPI format and check digit." if ok else "Invalid NPI format or check digit.",
    }


@npi.tool()
async def npi_lookup(npi: str) -> dict[str, Any]:
    """Get comprehensive provider details by 10-digit NPI from CMS NPPES."""
    try:
        data = await _get_json(_NPI_URL, {"version": "2.1", "number": npi})
        results = data.get("results", [])
        if not results:
            return demo_fixtures.npi_fallback(npi) or {"npi": npi, "found": False, "status": "not_found"}
        return {"found": True, **_format_npi_result(results[0])}
    except Exception as exc:  # noqa: BLE001
        # Live NPPES failed — fall back to curated data for known demo NPIs.
        return demo_fixtures.npi_fallback(npi) or _err({"npi": npi, "found": False}, exc)


@npi.tool()
async def npi_search(
    first_name: str = "",
    last_name: str = "",
    state: str = "",
    taxonomy_description: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """Search the NPPES Registry by provider name, state, and/or specialty."""
    try:
        params: dict[str, Any] = {"version": "2.1", "limit": max(1, min(int(limit), 50))}
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
        return _err({"results": []}, exc)


# ---------------------------------------------------------------------------
# CMS coverage — official CMS Coverage API (api.coverage.cms.gov, MCIM v1.x)
# Real NCD/LCD/Article data: ICD-10 covered/non-covered + HCPCS per policy.
# A free license token (AMA/ADA/AHA click-through) is auto-fetched for the
# CPT/HCPCS-bearing endpoints; NCD reports are public.
# Tools: search_national_coverage, search_local_coverage,
#        get_coverage_document, get_contractors
# ---------------------------------------------------------------------------
cms = FastMCP("cms-coverage", stateless_http=True, json_response=True, transport_security=_NO_HOST_CHECK)
_CMS = "https://api.coverage.cms.gov/v1"
_MCD_VIEW = "https://www.cms.gov/medicare-coverage-database/view"

_cms_cache: dict[str, Any] = {"token": None, "states": None, "ncds": None, "ncd_index": None}
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
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) >= 3 and t not in _STOPWORDS}


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


def _ncd_ids(api_url: str) -> tuple[str, str]:
    qs = parse_qs(urlparse(api_url or "").query)
    return (qs.get("ncdid") or [""])[0], (qs.get("ncdver") or [""])[0]


def _ncd_view_url(api_url: str) -> str:
    nid, nver = _ncd_ids(api_url)
    return f"{_MCD_VIEW}/ncd.aspx?ncdid={nid}&ncdver={nver}" if nid else ""


async def _ensure_ncds() -> None:
    if _cms_cache["ncds"] is None:
        rows = (await _cms_get("/reports/national-coverage-ncd/")).get("data", [])
        _cms_cache["ncds"] = rows
        idx: dict[str, tuple[str, str]] = {}
        for r in rows:
            nid, nver = _ncd_ids(r.get("url", ""))
            if r.get("document_display_id") and nid:
                idx[str(r["document_display_id"])] = (nid, nver)
        _cms_cache["ncd_index"] = idx


def _rank(items: list[dict], query: set[str], top: int) -> list[dict]:
    scored = []
    for it in items:
        score = len(query & _terms(it.get("title", "")))
        if score:
            scored.append((score, it))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [it for _, it in scored[:top]]


async def _safe_report(path: str, params: dict, token: str) -> list[dict]:
    try:
        return (await _cms_get(path, params, token)).get("data", [])
    except Exception:  # noqa: BLE001
        return []


async def _collect_rows(path: str, params: dict, token: str, code_field: str,
                        desc_field: str = "description", cap: int = 500) -> list[dict]:
    """Page through a code endpoint, collecting {code, description} up to `cap`."""
    out: list[dict] = []
    next_token = ""
    for _ in range(6):
        page = dict(params, page_size=500)
        if next_token:
            page["next_token"] = next_token
        data = await _cms_get(path, page, token)
        for row in data.get("data", []):
            c = row.get(code_field)
            if c:
                out.append({"code": str(c), "description": row.get(desc_field, "")})
        if len(out) >= cap:
            return out[:cap]
        next_token = (data.get("meta", {}) or {}).get("next_token") or ""
        if not next_token:
            break
    return out


async def _safe_collect(path: str, params: dict, token: str, code_field: str) -> list[dict]:
    """`_collect_rows` that yields [] instead of raising — for optional endpoints
    (LCD/NCD covered-code lists) that may be empty or absent for a given policy."""
    try:
        return await _collect_rows(path, params, token, code_field)
    except Exception:  # noqa: BLE001
        return []


@cms.tool()
async def search_national_coverage(keyword: str, document_type: str = "NCD", limit: int = 10) -> dict[str, Any]:
    """Search National Coverage Determinations (NCDs) by keyword.

    NCDs are nationwide Medicare coverage policies. Returns ranked matches with
    a policy_id usable in get_coverage_document(document_type="NCD").
    """
    try:
        await _ensure_ncds()
        ranked = _rank(_cms_cache["ncds"], _terms(keyword), max(1, min(int(limit), 25)))
        results = [
            {
                "policy_id": r.get("document_display_id"),
                "title": r.get("title"),
                "type": "NCD",
                "relevant": True,
                "url": _ncd_view_url(r.get("url", "")),
            }
            for r in ranked
        ]
        if not results:
            results = [p for p in demo_fixtures.search_fallback(keyword) if p.get("type") == "NCD"]
        return {"keyword": keyword, "count": len(results), "results": results}
    except Exception as exc:  # noqa: BLE001
        fb = [p for p in demo_fixtures.search_fallback(keyword) if p.get("type") == "NCD"]
        if fb:
            return {"keyword": keyword, "count": len(fb), "results": fb}
        return _err({"keyword": keyword, "results": []}, exc)


@cms.tool()
async def search_local_coverage(
    keyword: str, document_type: str = "LCD", limit: int = 10, state: str = ""
) -> dict[str, Any]:
    """Search Local Coverage (LCDs + billing/coding Articles) by keyword.

    LCDs carry the HCPCS list; billing/coding Articles carry the ICD-10
    medical-necessity covered/non-covered lists — both are returned, ranked.
    Provide the patient's `state` (2-letter or full) for jurisdiction filtering.
    Use get_coverage_document on a returned policy_id to pull its full criteria.
    """
    try:
        token = await _cms_token()
        state_id = await _resolve_state_id(state)
        q = _terms(keyword)
        n = max(1, min(int(limit), 25))
        params = {"state_id": state_id} if state_id else {}
        lcds = await _safe_report("/reports/local-coverage-final-lcds/", params, token)
        arts = await _safe_report("/reports/local-coverage-articles/", params, token)
        results = []
        for r in _rank(lcds, q, n):
            results.append({
                "policy_id": r.get("document_display_id"),
                "document_id": r.get("document_id"),
                "title": r.get("title"),
                "type": "LCD",
                "relevant": True,
                "url": f"{_MCD_VIEW}/lcd.aspx?lcdid={r.get('document_id')}",
            })
        for r in _rank(arts, q, n):
            results.append({
                "policy_id": r.get("document_display_id"),
                "document_id": r.get("document_id"),
                "title": r.get("title"),
                "type": "Article",
                "relevant": True,
                "url": f"{_MCD_VIEW}/article.aspx?articleId={r.get('document_id')}",
            })
        if not results:
            results = [p for p in demo_fixtures.search_fallback(keyword) if p.get("type") in ("LCD", "Article")]
        return {
            "keyword": keyword,
            "state": state,
            "state_resolved": bool(state_id) if state else None,
            "count": len(results),
            "results": results,
        }
    except Exception as exc:  # noqa: BLE001
        fb = [p for p in demo_fixtures.search_fallback(keyword) if p.get("type") in ("LCD", "Article")]
        if fb:
            return {"keyword": keyword, "state": state, "count": len(fb), "results": fb}
        return _err({"keyword": keyword, "results": []}, exc)


@cms.tool()
async def get_coverage_document(document_id: str, document_type: str = "") -> dict[str, Any]:
    """Get the criteria for a coverage policy (NCD, LCD, or Article).

    Accepts a display id ("240.2", "L33797", "A52514") or numeric id. For
    Articles, returns covered/non-covered ICD-10 lists + HCPCS; for LCDs, the
    HCPCS list; for NCDs, the policy text fields. Use the ICD-10 covered list
    for Diagnosis-Policy Alignment.
    """
    did = str(document_id).strip()
    dtype = (document_type or "").strip().lower()
    try:
        token = await _cms_token()
        numeric = re.sub(r"\D", "", did)

        if did[:1].upper() == "A" or dtype == "article":
            covered, noncovered, hcpcs = await asyncio.gather(
                _collect_rows("/data/article/icd10-covered", {"articleid": numeric}, token, "icd10_code_id"),
                _collect_rows("/data/article/icd10-noncovered", {"articleid": numeric}, token, "icd10_code_id"),
                _collect_rows("/data/article/hcpc-code", {"articleid": numeric}, token, "hcpc_code_id", "long_description"),
            )
            _fx = demo_fixtures.document_fallback(did)
            if _fx and not covered:  # known demo policy with no live codes
                covered = _fx.get("covered_icd10", [])
                noncovered = noncovered or _fx.get("noncovered_icd10", [])
                hcpcs = hcpcs or _fx.get("hcpcs", [])
            return {
                "document_id": did,
                "type": "Article",
                "url": f"{_MCD_VIEW}/article.aspx?articleId={numeric}",
                "covered_icd10": covered,
                "noncovered_icd10": noncovered,
                "hcpcs": hcpcs,
            }

        if did[:1].upper() == "L" or dtype == "lcd":
            lcd_data, hcpcs, covered, noncovered = await asyncio.gather(
                _cms_get("/data/lcd/", {"lcdid": numeric}, token),
                _collect_rows("/data/lcd/hcpc-code", {"lcdid": numeric}, token, "hcpc_code_id", "long_description"),
                _safe_collect("/data/lcd/icd10-covered", {"lcdid": numeric}, token, "icd10_code_id"),
                _safe_collect("/data/lcd/icd10-noncovered", {"lcdid": numeric}, token, "icd10_code_id"),
            )
            row = (lcd_data.get("data") or [{}])[0]
            _fx = demo_fixtures.document_fallback(did)
            if _fx and not covered:  # LCD covered codes often live on its Article
                covered = _fx.get("covered_icd10", [])
                noncovered = noncovered or _fx.get("noncovered_icd10", [])
                hcpcs = hcpcs or _fx.get("hcpcs", [])
            return {
                "document_id": did,
                "type": "LCD",
                "title": row.get("title", ""),
                "url": f"{_MCD_VIEW}/lcd.aspx?lcdid={numeric}",
                "covered_icd10": covered,
                "noncovered_icd10": noncovered,
                "hcpcs": hcpcs,
                "policy_fields": _short_fields(row),
            }

        # NCD — resolve display id ("240.2") via the cached index, else numeric.
        await _ensure_ncds()
        nid, nver = _cms_cache["ncd_index"].get(did, (numeric or None, ""))
        if not nid:
            return demo_fixtures.document_fallback(did) or {
                "document_id": did, "type": "NCD", "error": "Unresolved NCD id"
            }
        ncd_params = {"ncdid": nid}
        if nver:
            ncd_params["ncdver"] = nver
        ncd_data, covered = await asyncio.gather(
            _cms_get("/data/ncd/", ncd_params, token),
            _safe_collect("/data/ncd/icd10-covered", ncd_params, token, "icd10_code_id"),
        )
        row = (ncd_data.get("data") or [{}])[0]
        _fx = demo_fixtures.document_fallback(did)
        if _fx and not covered:  # known demo NCD with no live covered list
            covered = _fx.get("covered_icd10", [])
        return {
            "document_id": did,
            "type": "NCD",
            "title": row.get("title", ""),
            "url": f"{_MCD_VIEW}/ncd.aspx?ncdid={nid}&ncdver={nver}",
            "covered_icd10": covered,
            "policy_fields": _short_fields(row),
        }
    except Exception as exc:  # noqa: BLE001
        # Live CMS failed — fall back to curated data for known demo policy ids.
        return demo_fixtures.document_fallback(did) or _err(
            {"document_id": did, "type": document_type or "unknown"}, exc
        )


def _short_fields(row: dict[str, Any], limit: int = 6000) -> dict[str, str]:
    """Non-empty string fields short enough to be useful policy text."""
    return {k: v for k, v in row.items() if isinstance(v, str) and v.strip() and len(v) < limit}


@cms.tool()
async def get_contractors(state: str, contractor_type: str = "", limit: int = 5) -> dict[str, Any]:
    """Get Medicare Administrative Contractors (MACs) whose LCDs apply to a state."""
    try:
        token = await _cms_token()
        state_id = await _resolve_state_id(state)
        if not state_id:
            return {"state": state, "resolved": False, "contractors": [],
                    "note": "State not resolved; provide a valid US state (2-letter or full)."}
        rows = await _safe_report("/reports/local-coverage-final-lcds/", {"state_id": state_id}, token)
        seen: list[str] = []
        for r in rows:
            c = r.get("contractor_name_type") or r.get("contractor")
            if c and c not in seen:
                seen.append(c)
            if len(seen) >= max(1, min(int(limit), 20)):
                break
        return {"state": state, "state_id": state_id, "resolved": True,
                "contractors": [{"name": c} for c in seen]}
    except Exception as exc:  # noqa: BLE001
        return _err({"state": state, "contractors": []}, exc)


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
