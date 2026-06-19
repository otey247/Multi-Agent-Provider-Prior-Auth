"""Deterministic enrichment of the coverage agent's output.

The coverage agent (an LLM) is non-deterministic: run-to-run it may or may not
transcribe the provider's full taxonomy set, and may or may not match every
submitted code against a policy's covered/non-covered lists. For technical
demonstrations we need those two fields to be reliable, so the backend computes
them deterministically by calling the self-hosted medical-data MCP server
directly (the same data source the agent uses), then fills/overrides:

  * provider_verification.taxonomies / credential  (filled when the agent left
    them empty — real NPPES data, or the curated demo fallback)
  * per_code_coverage                              (recomputed: each submitted
    ICD-10 / procedure code matched against the matched policy's code lists)

It is best-effort: any failure (or an unset MEDICAL_MCP_BASE_URL) leaves the
agent's output untouched and never breaks the pipeline.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)

_BASE = (os.environ.get("MEDICAL_MCP_BASE_URL", "") or "").strip().rstrip("/")

# Procedure code -> Medicare policy id for the demo sample cases. Lets the
# per-code matrix resolve directly (and fast) to the right policy without a slow
# CMS keyword search; get_coverage_document fills the covered codes (curated
# fallback when the live policy carries none). Real cases use the agent's
# returned policies instead.
_DEMO_POLICY_BY_CODE = {
    "22612": "L37848", "22840": "L37848",   # lumbar spinal fusion
    "J9303": "110.17", "96413": "110.17",    # colorectal chemo (panitumumab)
    "E1390": "240.2", "E0431": "240.2",      # home oxygen
    "31628": "BRONCH-DX",                     # bronchoscopic biopsy
}

# Hard ceiling so enrichment can never push the request past the gateway timeout.
_ENRICH_TIMEOUT_S = 40.0


def _norm(code: str) -> str:
    return (code or "").replace(".", "").strip().upper()


async def _mcp_call(domain: str, tool: str, args: dict) -> dict:
    """Single MCP streamable-HTTP tool call against the medical-data server."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = f"{_BASE}/{domain}/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
            text = result.content[0].text if result.content else "{}"
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return {}


async def _enrich_provider(coverage_result: dict, request_data: dict) -> None:
    npi = str(
        request_data.get("provider_npi")
        or request_data.get("ordering_provider_npi")
        or ""
    ).strip()
    if not npi:
        return
    look = await _mcp_call("npi", "npi_lookup", {"npi": npi})
    if not look.get("found"):
        return
    pv = coverage_result.get("provider_verification") or {}
    if not pv.get("taxonomies") and look.get("taxonomies"):
        pv["taxonomies"] = look["taxonomies"]
    if not pv.get("credential") and look.get("credential"):
        pv["credential"] = look["credential"]
    if not pv.get("specialty") and look.get("specialty"):
        pv["specialty"] = look["specialty"]
    if not pv.get("name") and look.get("name"):
        pv["name"] = look["name"]
    pv.setdefault("npi", npi)
    coverage_result["provider_verification"] = pv


async def _enrich_per_code(coverage_result: dict, request_data: dict) -> None:
    # Candidate policies = the agent's own policies + a small code->policy map for
    # the demo sample cases (resolved directly, no slow CMS keyword search — the
    # earlier search-based approach blew the request timeout). get_coverage_document
    # on a demo policy id returns its covered codes via the MCP server's curated
    # fallback when the live policy carries none.
    candidates: list[dict] = []
    seen: set[str] = set()

    def _add(pid, ptype: str = "") -> None:
        if pid and str(pid) not in seen:
            seen.add(str(pid))
            candidates.append({"policy_id": str(pid), "type": ptype})

    for code in request_data.get("procedure_codes", []):
        _add(_DEMO_POLICY_BY_CODE.get(str(code).upper().strip()))
    for p in coverage_result.get("coverage_policies") or []:
        if isinstance(p, dict):
            _add(p.get("policy_id") or p.get("document_id"), p.get("type", ""))

    covered: set[str] = set()
    noncovered: set[str] = set()
    hcpcs: set[str] = set()
    used_policy = ""
    for p in candidates[:6]:
        pid = p.get("policy_id") or p.get("document_id")
        if not pid:
            continue
        doc = await _mcp_call(
            "cms_coverage", "get_coverage_document",
            {"document_id": str(pid), "document_type": p.get("type", "")},
        )
        c = {_norm(x.get("code")) for x in doc.get("covered_icd10", []) if isinstance(x, dict)}
        n = {_norm(x.get("code")) for x in doc.get("noncovered_icd10", []) if isinstance(x, dict)}
        h = {_norm(x.get("code")) for x in doc.get("hcpcs", []) if isinstance(x, dict)}
        if c or h:
            covered |= c
            noncovered |= n
            hcpcs |= h
            used_policy = str(p.get("policy_id") or pid)
            break  # first policy with real code lists wins

    if not used_policy:
        return

    pcc: list[dict] = []
    for dx in request_data.get("diagnosis_codes", []):
        k = _norm(dx)
        status = "covered" if k in covered else ("non_covered" if k in noncovered else "not_listed")
        pcc.append({"code": dx, "code_type": "ICD10", "status": status, "policy_id": used_policy})
    for px in request_data.get("procedure_codes", []):
        k = _norm(px)
        status = "covered" if k in hcpcs else "not_listed"
        pcc.append({"code": px, "code_type": "HCPCS", "status": status, "policy_id": used_policy})
    coverage_result["per_code_coverage"] = pcc


async def _run_enrich(coverage_result: dict, request_data: dict) -> None:
    try:
        await _enrich_provider(coverage_result, request_data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("provider taxonomy enrichment skipped: %s", exc)
    try:
        await _enrich_per_code(coverage_result, request_data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("per-code coverage enrichment skipped: %s", exc)


async def enrich_coverage(coverage_result: dict, request_data: dict) -> dict:
    """Deterministically fill provider taxonomies + per-code coverage. No-op on
    failure, timeout, or when MEDICAL_MCP_BASE_URL is unset."""
    if not _BASE or not isinstance(coverage_result, dict):
        return coverage_result
    try:
        await asyncio.wait_for(
            _run_enrich(coverage_result, request_data), timeout=_ENRICH_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        logger.warning("coverage enrichment timed out (%ss); using agent output", _ENRICH_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning("coverage enrichment skipped: %s", exc)
    return coverage_result
