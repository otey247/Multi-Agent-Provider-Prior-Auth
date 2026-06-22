"""Multi-Agent Orchestrator for Provider Prior Authorization Preparation.

Coordinates three specialized agents in a fan-out/fan-in pattern:
  Phase 1 (parallel): Documentation Completeness Agent + Clinical Evidence Retrieval Agent
  Phase 2 (sequential): Policy Matching Agent (receives clinical findings)
  Phase 3: Submission Readiness Assessment — aggregates all agent outputs
  Phase 4: Builds submission readiness report and audit trail

Provider-side prior auth workflow:
  - Documentation Completeness: validates that the request package has all required fields
  - Clinical Evidence Retrieval: extracts and validates clinical evidence from notes
  - Policy Matching: verifies provider credentials and matches evidence to payer requirements
  - Submission Readiness Assessment: determines if the package is ready to submit to payer

Gate-based submission readiness evaluation:
  - Gate 1: Provider credential check
  - Gate 2: Code and order validation
  - Gate 3: Payer policy requirements matching
  - Confidence scoring: HIGH/MEDIUM/LOW + 0-100
  - Audit trail with data sources and metrics
  - Submission readiness report generation

All four specialist agents (documentation completeness, clinical evidence retrieval,
policy matching, submission readiness) run as independent hosted agent containers.
This module is the pure dispatcher.
"""

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from app.agents.compliance_agent import run_compliance_review
from app.agents.clinical_agent import run_clinical_review
from app.agents.coverage_agent import run_coverage_review
from app.agents.synthesis_agent import run_synthesis_review as _dispatch_synthesis
from app.services.audit_pdf import generate_audit_justification_pdf
from app.services.coverage_enrich import enrich_coverage
from app.services.cpt_validation import validate_procedure_codes

logger = logging.getLogger(__name__)

# OpenTelemetry tracer for custom spans (no-op if observability not configured)
try:
    from agent_framework.observability import get_tracer
    tracer = get_tracer(__name__)
except ImportError:
    from contextlib import contextmanager

    class _NoOpSpan:
        """Minimal no-op span for when observability is not installed."""
        def set_attribute(self, key: str, value: object) -> None: ...
        def set_status(self, *args: object, **kwargs: object) -> None: ...
        def record_exception(self, exc: BaseException) -> None: ...
        def __enter__(self): return self
        def __exit__(self, *args): ...

    class _NoOpTracer:
        @contextmanager
        def start_as_current_span(self, name: str, **kwargs):
            yield _NoOpSpan()

    tracer = _NoOpTracer()  # type: ignore[assignment]

# Maximum number of retries when an agent returns an incomplete result
_MAX_AGENT_RETRIES = 1

# Expected top-level keys for each agent result.
# If any of these are missing the result is considered incomplete/truncated.
_EXPECTED_KEYS: dict[str, set[str]] = {
    "Compliance Agent": {"checklist", "overall_status"},
    "Clinical Reviewer Agent": {
        "diagnosis_validation",
        "clinical_extraction",
        "clinical_summary",
    },
    "Coverage Agent": {"provider_verification", "criteria_assessment"},
}


def _validate_agent_result(agent_name: str, result: dict) -> list[str]:
    """Check that an agent result contains the expected top-level keys.

    Returns a list of missing key names (empty list means valid).
    """
    if result.get("error"):
        return [f"error: {result['error']}"]

    expected = _EXPECTED_KEYS.get(agent_name, set())
    if not expected:
        return []

    missing = [k for k in expected if k not in result]
    return missing


_AGENT_DISPLAY_NAMES: dict[str, str] = {
    "compliance": "Documentation Completeness Agent",
    "clinical": "Clinical Evidence Retrieval Agent",
    "coverage": "Policy Matching Agent",
}

_CHECKLIST_STATUS_MAP: dict[str, str] = {
    "complete": "pass",
    "incomplete": "warning",
    "missing": "fail",
}

_FALLBACK_TOOL_NAMES = {
    "foundry_clinical_agent",
    "clinical_fallback",
    "foundry_coverage_agent",
    "coverage_fallback",
}


def _enrich_agent_result(agent_key: str, result: dict) -> dict:
    """Inject ``agent_name`` and ``checks_performed`` into an agent result dict.

    The frontend's AgentDetails component expects both fields on every
    agent result.  Since the hosted agents do not emit them (they are not
    part of the SKILL.md output schemas), we derive them here:

    - ``agent_name``      — human-readable display name for the agent.
    - ``checks_performed`` — for compliance: mapped from the ``checklist``
      items (complete→pass, incomplete→warning, missing→fail);
      for clinical/coverage: mapped from ``tool_results``
      (tool_name→rule, status→result, detail kept as-is).
    """
    if not result or result.get("error"):
        return result

    enriched = dict(result)
    enriched.setdefault("agent_name", _AGENT_DISPLAY_NAMES.get(agent_key, agent_key))

    if "checks_performed" not in enriched:
        if agent_key == "compliance":
            # Derive from checklist (compliance has no tool_results)
            checks_performed = [
                {
                    "rule": item.get("item", ""),
                    "result": _CHECKLIST_STATUS_MAP.get(item.get("status", ""), "info"),
                    "detail": item.get("detail", ""),
                }
                for item in enriched.get("checklist", [])
            ]
        else:
            # Derive from tool_results (clinical + coverage)
            checks_performed = [
                {
                    "rule": tr.get("tool_name", ""),
                    "result": tr.get("status", "info"),
                    "detail": tr.get("detail", ""),
                }
                for tr in enriched.get("tool_results", [])
            ]
        enriched["checks_performed"] = checks_performed

    return enriched


# --- In-memory review store (demo persistence) ---
_review_store: dict[str, dict] = {}


def store_review(request_id: str, request_data: dict, response: dict) -> None:
    """Persist a completed review for later retrieval."""
    _review_store[request_id] = {
        "request_id": request_id,
        "request_data": request_data,
        "response": response,
        "decision": None,
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }


def get_review(request_id: str) -> dict | None:
    """Retrieve a stored review by request_id."""
    return _review_store.get(request_id)


def list_reviews() -> list[dict]:
    """List all stored reviews (most recent first)."""
    return sorted(
        _review_store.values(),
        key=lambda r: r["stored_at"],
        reverse=True,
    )


def store_decision(request_id: str, decision: dict) -> None:
    """Attach a decision to a stored review."""
    if request_id in _review_store:
        _review_store[request_id]["decision"] = decision


def _compute_confidence(
    compliance_result: dict,
    clinical_result: dict,
    coverage_result: dict,
) -> tuple[float, str]:
    """Compute overall confidence score and level from agent results."""
    scores = []

    # Extraction confidence from clinical agent
    extraction = clinical_result.get("clinical_extraction", {})
    if isinstance(extraction, dict):
        ext_conf = extraction.get("extraction_confidence", 50)
        scores.append(ext_conf / 100.0)

    # Per-criterion confidence from coverage agent
    criteria = coverage_result.get("criteria_assessment", [])
    if criteria:
        criterion_scores = [
            c.get("confidence", 50) / 100.0
            for c in criteria
            if isinstance(c, dict)
        ]
        if criterion_scores:
            scores.append(sum(criterion_scores) / len(criterion_scores))

    # Compliance completeness bonus/penalty
    compliance_status = compliance_result.get("overall_status", "incomplete")
    missing = compliance_result.get("missing_items", [])
    if compliance_status == "complete" and not missing:
        scores.append(1.0)
    else:
        penalty = max(0.0, 1.0 - 0.1 * len(missing))
        scores.append(penalty)

    # Agent error penalties
    for result in [compliance_result, clinical_result, coverage_result]:
        if result.get("error"):
            scores.append(0.0)

    if not scores:
        return 0.5, "MEDIUM"

    confidence = sum(scores) / len(scores)
    confidence = max(0.0, min(1.0, confidence))

    if confidence >= 0.80:
        level = "HIGH"
    elif confidence >= 0.50:
        level = "MEDIUM"
    else:
        level = "LOW"

    return round(confidence, 2), level


def _normalize_coverage_result(coverage_result: dict) -> dict:
    """Lightweight pass-through for coverage agent output.

    With structured output (output_format), the coverage agent returns data
    matching the CoverageResult Pydantic schema directly. This function
    only normalizes the provider_verification status field for display
    consistency (e.g., 'A' -> 'VERIFIED').
    """
    if coverage_result.get("error"):
        return coverage_result

    result = dict(coverage_result)

    # Normalize provider_verification status for display
    pv = result.get("provider_verification")
    if pv and isinstance(pv, dict):
        status = str(pv.get("status", "")).upper()
        if status in ("A", "ACTIVE", "VERIFIED"):
            pv["status"] = "VERIFIED"
        elif status in ("D", "DEACTIVATED", "INACTIVE"):
            pv["status"] = "INACTIVE"

    return result


def _uses_agent_fallback(result: dict) -> bool:
    """Return True when a result was produced by local conservative fallback."""
    return bool(result.get("_fallback_reason"))


def _is_fallback_tool_result(tool_result: dict) -> bool:
    """Return True for diagnostic fallback tool results kept in agent details only."""
    return str(tool_result.get("tool_name", "")).lower() in _FALLBACK_TOOL_NAMES


def _normalize_recommendation_value(value) -> str:
    """Normalize agent recommendation variants to the API contract."""
    normalized = str(value or "needs_review").strip().lower().replace(" ", "_")
    if normalized in ("approve", "approved", "ready", "ready_to_submit"):
        return "ready_to_submit"
    if normalized in ("pend", "pended", "pend_for_review", "needs_review", "manual_review"):
        return "needs_review"
    return normalized or "needs_review"


def _apply_fallback_synthesis_guardrails(
    synthesis: dict,
    clinical_result: dict,
    coverage_result: dict,
) -> dict:
    """Keep top-level synthesis conservative when upstream agents used fallback."""
    if not (_uses_agent_fallback(clinical_result) or _uses_agent_fallback(coverage_result)):
        return synthesis

    guarded = dict(synthesis)
    guarded["recommendation"] = "needs_review"
    try:
        confidence = float(guarded.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    guarded["confidence"] = min(confidence, 0.35)
    guarded["confidence_level"] = "LOW"

    if _uses_agent_fallback(coverage_result):
        guarded["coverage_criteria_met"] = []
        guarded["coverage_criteria_not_met"] = [
            "Provider credential verification not completed - manual NPI/payer verification required",
            "Payer policy and medical necessity match not completed - manual policy review required",
        ]
        guarded["criteria_summary"] = (
            "0 payer policy requirements verified; manual review required"
        )
        guarded["decision_gate"] = "manual_review_required"

    missing = list(guarded.get("missing_documentation") or [])
    required_items = [
        "Manual provider credential verification against NPPES or payer records",
        "Manual payer policy review confirming applicable LCD/NCD or payer-specific criteria",
    ]
    if _uses_agent_fallback(clinical_result):
        required_items.append(
            "Manual clinical evidence review because ICD-10, PubMed, and clinical-trial MCP checks did not complete"
        )
    for item in required_items:
        if item not in missing:
            missing.append(item)
    guarded["missing_documentation"] = missing

    fallback_note = (
        "Hosted clinical and/or coverage agents were unavailable, so this result "
        "uses conservative local fallback data. Treat all credential, diagnosis "
        "billability, literature, trial, and payer-policy findings as not verified "
        "until staff completes manual review."
    )
    existing_rationale = str(guarded.get("clinical_rationale") or "").strip()
    if fallback_note not in existing_rationale:
        guarded["clinical_rationale"] = (
            f"{fallback_note}\n\n{existing_rationale}" if existing_rationale else fallback_note
        )

    return guarded


def _is_hosted_agent_error(result: dict) -> bool:
    """Return True for transport/runtime errors from hosted agent invocation."""
    error = str(result.get("error") or "")
    if not error:
        return False
    return any(
        marker in error
        for marker in (
            "Foundry Hosted Agent",
            "Hosted clinical-reviewer-agent call failed",
            "Hosted coverage-assessment-agent call failed",
            "call timed out",
            "is not reachable",
        )
    )


def _fallback_detail(agent_label: str) -> str:
    """User-facing status for conservative local fallback."""
    return (
        f"{agent_label} hosted runtime unavailable; "
        "using conservative local fallback. Manual verification required."
    )


def _looks_like_icd10(code: str) -> bool:
    """Basic ICD-10-CM format check used only when the clinical agent is down."""
    return bool(re.match(r"^[A-Z][0-9][A-Z0-9](?:\.[A-Z0-9]{1,4})?$", code.strip().upper()))


def _first_sentence(text: str, max_len: int = 240) -> str:
    """Extract a short human-readable note snippet for fallback summaries."""
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ""

    parts = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)
    return parts[0][:max_len]


def _coerce_list(value) -> list[str]:
    """Return a list of strings from user-provided scalar/list values."""
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value:
        return [str(value)]
    return []


def _build_clinical_fallback_result(
    request_data: dict,
    cpt_validation: dict,
    error_detail: str,
) -> dict:
    """Build a conservative clinical result when the hosted clinical agent fails."""
    diagnosis_codes = [str(code).strip().upper() for code in request_data.get("diagnosis_codes", [])]
    notes = str(request_data.get("clinical_notes") or "")
    prior_treatments = _coerce_list(request_data.get("prior_treatment_history"))
    note_snippet = _first_sentence(notes)

    diagnosis_validation = [
        {
            "code": code,
            "valid": _looks_like_icd10(code),
            "description": "Format-only validation; ICD-10 MCP lookup was not completed.",
            "billable": False,
            "hierarchy_note": "Billable status requires ICD-10 MCP verification.",
        }
        for code in diagnosis_codes
    ]

    procedure_validation = [
        {
            "code": item.get("code", ""),
            "valid": bool(item.get("valid_format")),
            "description": item.get("description") or item.get("detail", ""),
            "source": "orchestrator_preflight",
        }
        for item in cpt_validation.get("results", [])
        if isinstance(item, dict)
    ]

    extraction_confidence = 35 if notes.strip() else 10
    if prior_treatments:
        extraction_confidence += 10

    return {
        "agent_name": "Clinical Reviewer Agent",
        "diagnosis_validation": diagnosis_validation,
        "procedure_validation": procedure_validation,
        "clinical_extraction": {
            "chief_complaint": note_snippet,
            "history_of_present_illness": notes[:1000],
            "prior_treatments": prior_treatments,
            "severity_indicators": [],
            "functional_limitations": [],
            "diagnostic_findings": [],
            "duration_and_progression": "",
            "medical_history_and_comorbidities": "",
            "extraction_confidence": min(extraction_confidence, 45),
        },
        "literature_support": [],
        "clinical_trials": [],
        "clinical_summary": (
            "Hosted clinical review was unavailable. Local fallback used submitted "
            "clinical notes and CPT/HCPCS preflight only; ICD-10, PubMed, and "
            "ClinicalTrials.gov checks require manual review."
        ),
        "tool_results": [
            {
                "tool_name": "foundry_clinical_agent",
                "status": "info",
                "detail": _fallback_detail("Clinical"),
            },
            {
                "tool_name": "clinical_fallback",
                "status": "info",
                "detail": "Local note extraction used; external clinical MCP tools were not completed.",
            },
        ],
        "checks_performed": [],
        "_fallback_reason": _fallback_detail("Clinical"),
        "_hosted_agent_error": str(error_detail)[:500],
    }


def _build_coverage_fallback_result(
    request_data: dict,
    clinical_result: dict,
    error_detail: str,
) -> dict:
    """Build conservative coverage output when the hosted coverage agent fails."""
    provider_npi = str(
        request_data.get("ordering_provider_npi")
        or request_data.get("provider_npi")
        or ""
    )
    provider_name = str(request_data.get("ordering_provider_name") or "")
    specialty = str(request_data.get("rendering_provider_specialty") or "")
    provider_display = provider_name or (f"NPI {provider_npi}" if provider_npi else "")

    notes_present = bool(str(request_data.get("clinical_notes") or "").strip())
    clinical_extraction = clinical_result.get("clinical_extraction") or {}
    if not isinstance(clinical_extraction, dict):
        clinical_extraction = {}
    has_clinical_evidence = notes_present or bool(clinical_extraction.get("chief_complaint"))

    criteria = [
        {
            "criterion": "Provider credential verification",
            "status": "INSUFFICIENT",
            "confidence": 20,
            "evidence": [f"NPI provided: {provider_npi}" if provider_npi else "No NPI available"],
            "notes": "NPI registry lookup was not completed because the hosted coverage agent was unavailable.",
            "source": "coverage_fallback",
            "met": False,
        },
        {
            "criterion": "Payer policy and medical necessity match",
            "status": "INSUFFICIENT",
            "confidence": 25 if has_clinical_evidence else 10,
            "evidence": ["Submitted clinical notes present"] if has_clinical_evidence else ["Clinical evidence not available"],
            "notes": "CMS coverage policy search was not completed. Human review must verify payer criteria.",
            "source": "coverage_fallback",
            "met": False,
        },
    ]

    return {
        "agent_name": "Coverage Agent",
        "provider_verification": {
            "npi": provider_npi,
            "name": provider_display,
            "specialty": specialty,
            "status": "UNVERIFIED",
            "detail": "NPI registry lookup not completed; verify provider credentials manually.",
        },
        "coverage_policies": [],
        "criteria_assessment": criteria,
        "coverage_criteria_met": [],
        "coverage_criteria_not_met": [
            c["criterion"] for c in criteria if c.get("source") == "coverage_fallback"
        ],
        "policy_references": [],
        "coverage_limitations": [
            "Coverage policy search unavailable; payer requirements must be checked manually."
        ],
        "documentation_gaps": [
            {
                "what": "Manual provider credential verification required",
                "critical": True,
                "request": "Verify NPI, provider status, and specialty against NPPES or payer records.",
            },
            {
                "what": "Manual payer policy criteria review required",
                "critical": True,
                "request": "Confirm applicable LCD/NCD or payer-specific prior authorization criteria.",
            },
        ],
        "tool_results": [
            {
                "tool_name": "foundry_coverage_agent",
                "status": "info",
                "detail": _fallback_detail("Coverage"),
            },
            {
                "tool_name": "coverage_fallback",
                "status": "info",
                "detail": "Local conservative criteria used; NPI and CMS coverage MCP tools were not completed.",
            },
        ],
        "checks_performed": [],
        "_fallback_reason": _fallback_detail("Coverage"),
        "_hosted_agent_error": str(error_detail)[:500],
    }


def _build_audit_trail(
    compliance_result: dict,
    clinical_result: dict,
    coverage_result: dict,
    start_time: str,
    synthesis: dict | None = None,
) -> dict:
    """Build audit trail from agent results."""
    data_sources = ["CPT/HCPCS Format Validation (Local)"]
    clinical_fallback = _uses_agent_fallback(clinical_result)
    coverage_fallback = _uses_agent_fallback(coverage_result)

    if clinical_fallback:
        data_sources.append("Clinical fallback extraction (Local)")
    if coverage_fallback:
        data_sources.append("Coverage fallback assessment (Local)")

    # Check which MCP tools were used via tool_results
    for result in [clinical_result, coverage_result]:
        for tr in result.get("tool_results", []):
            if _is_fallback_tool_result(tr):
                continue

            tool = tr.get("tool_name", "")
            tool_lower = tool.lower()
            if "npi" in tool_lower:
                source = "NPI Registry MCP (NPPES)"
            elif "icd10" in tool_lower or "icd-10" in tool_lower or "validate_code" in tool_lower or "lookup_code" in tool_lower:
                source = "ICD-10 MCP (2026 Code Set)"
            elif "coverage" in tool_lower or "cms" in tool_lower or "lcd" in tool_lower or "ncd" in tool_lower:
                source = "CMS Coverage MCP (LCDs/NCDs)"
            elif "trial" in tool_lower or "clinical_trial" in tool_lower or "clinical-trial" in tool_lower:
                source = "ClinicalTrials.gov MCP"
            elif "pubmed" in tool_lower:
                source = "PubMed MCP (Biomedical Literature)"
            elif "search" in tool_lower:
                # Generic "search" — likely PubMed search
                source = "PubMed MCP (Biomedical Literature)"
            else:
                source = f"MCP Tool: {tool}"
            if source not in data_sources:
                data_sources.append(source)

    # Always infer data sources from result data to supplement tool_results
    # (agents may not always report tool_results for every MCP call)

    # If provider verification has data, NPI registry was used unless it came
    # from local fallback, where the registry lookup explicitly did not run.
    pv = coverage_result.get("provider_verification", {})
    if not coverage_fallback and pv and isinstance(pv, dict) and pv.get("npi"):
        if "NPI Registry MCP (NPPES)" not in data_sources:
            data_sources.append("NPI Registry MCP (NPPES)")

    # If diagnosis validation has data, ICD-10 MCP was used unless it came
    # from local fallback format-only validation.
    dx = clinical_result.get("diagnosis_validation", [])
    if not clinical_fallback and dx:
        if "ICD-10 MCP (2026 Code Set)" not in data_sources:
            data_sources.append("ICD-10 MCP (2026 Code Set)")

    # If coverage policies found, CMS Coverage MCP was used unless coverage
    # fallback is active.
    policies = coverage_result.get("coverage_policies", [])
    if not coverage_fallback and policies:
        if "CMS Coverage MCP (LCDs/NCDs)" not in data_sources:
            data_sources.append("CMS Coverage MCP (LCDs/NCDs)")

    # If literature support found, PubMed was used
    lit = clinical_result.get("literature_support", [])
    if lit:
        if "PubMed MCP (Biomedical Literature)" not in data_sources:
            data_sources.append("PubMed MCP (Biomedical Literature)")

    # If clinical trials found, ClinicalTrials.gov was used
    trials = clinical_result.get("clinical_trials", [])
    if trials:
        if "ClinicalTrials.gov MCP" not in data_sources:
            data_sources.append("ClinicalTrials.gov MCP")

    # In a fully hosted run all 5 MCP sources are queried even if they return
    # no results. During fallback, list only the local fallback sources above
    # so the audit trail does not imply unavailable MCP tools were consulted.
    if not (clinical_fallback or coverage_fallback):
        for source in [
            "NPI Registry MCP (NPPES)",
            "ICD-10 MCP (2026 Code Set)",
            "CMS Coverage MCP (LCDs/NCDs)",
            "PubMed MCP (Biomedical Literature)",
            "ClinicalTrials.gov MCP",
        ]:
            if source not in data_sources:
                data_sources.append(source)

    # Extraction confidence
    extraction = clinical_result.get("clinical_extraction", {})
    ext_conf = extraction.get("extraction_confidence", 0) if isinstance(extraction, dict) else 0

    # Assessment confidence (avg of criterion confidences)
    criteria = coverage_result.get("criteria_assessment", [])

    # If coverage agent didn't provide criteria, try synthesis
    if not criteria and synthesis:
        criteria = synthesis.get("criteria_assessment", [])

    if criteria:
        conf_scores = [c.get("confidence", 0) for c in criteria if isinstance(c, dict)]
        assess_conf = int(sum(conf_scores) / len(conf_scores)) if conf_scores else 0
    else:
        assess_conf = 0

    # Criteria met count (case-insensitive — agents may return lowercase)
    met = sum(1 for c in criteria if isinstance(c, dict) and str(c.get("status", "")).upper() == "MET")
    total = len(criteria)
    criteria_met_count = f"{met}/{total}" if total else "0/0"

    # If criteria_met_count is 0/0 but synthesis has criteria data, use it
    if criteria_met_count == "0/0" and synthesis:
        syn_met = synthesis.get("coverage_criteria_met", [])
        syn_not_met = synthesis.get("coverage_criteria_not_met", [])
        if syn_met or syn_not_met:
            criteria_met_count = f"{len(syn_met)}/{len(syn_met) + len(syn_not_met)}"
            if assess_conf == 0 and syn_met:
                # Estimate from synthesis confidence
                assess_conf = int(synthesis.get("confidence", 0.5) * 100)

    return {
        "data_sources": data_sources,
        "review_started": start_time,
        "review_completed": datetime.now(timezone.utc).isoformat(),
        "extraction_confidence": ext_conf,
        "assessment_confidence": assess_conf,
        "criteria_met_count": criteria_met_count,
    }


def _generate_audit_justification(
    request_data: dict,
    synthesis: dict,
    compliance_result: dict,
    clinical_result: dict,
    coverage_result: dict,
    audit_trail: dict,
) -> str:
    """Generate an audit justification document in Markdown format.

    Based on the Anthropic prior-auth-review-skill audit_justification.md template.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    recommendation_raw = str(synthesis.get("recommendation", "needs_review"))
    # Normalize legacy values for display
    if recommendation_raw in ("approve",):
        recommendation_display = "READY TO SUBMIT"
    elif recommendation_raw in ("pend_for_review",):
        recommendation_display = "NEEDS REVIEW"
    else:
        recommendation_display = recommendation_raw.upper().replace("_", " ")
    confidence = synthesis.get("confidence", 0)
    try:
        confidence = float(confidence)
    except (ValueError, TypeError):
        confidence = 0.0
    confidence_level = synthesis.get("confidence_level", "LOW")

    lines = []

    # --- Disclaimer Header ---
    lines.append("# Provider Prior Authorization — Submission Readiness Report")
    lines.append("")
    lines.append("> **WARNING: AI-ASSISTED DRAFT — REVIEW REQUIRED**")
    lines.append("> All assessments are drafts requiring human clinical review before submission.")
    lines.append("> Payer policy matching reflects Medicare LCDs/NCDs only.")
    lines.append("> Commercial and Medicare Advantage plans may have different requirements.")
    lines.append("")

    # --- Section 1: Executive Summary ---
    lines.append("## 1. Executive Summary")
    lines.append("")
    lines.append(f"- **Assessment Date:** {now}")
    lines.append(f"- **Patient:** {request_data.get('patient_name', 'N/A')} (DOB: {request_data.get('patient_dob', 'N/A')})")
    lines.append(f"- **Provider NPI:** {request_data.get('provider_npi', 'N/A')}")
    lines.append(f"- **Insurance ID:** {request_data.get('insurance_id') or 'Not provided'}")
    lines.append(f"- **Diagnosis Codes:** {', '.join(request_data.get('diagnosis_codes', []))}")
    lines.append(f"- **Procedure Codes:** {', '.join(request_data.get('procedure_codes', []))}")
    lines.append(f"- **Submission Status:** {recommendation_display}")
    lines.append(f"- **Confidence:** {confidence_level} ({int(confidence * 100)}%)")
    lines.append("")
    lines.append(f"**Summary:** {synthesis.get('summary', 'N/A')}")
    lines.append("")

    # --- Section 2: Clinical Evidence Assessment ---
    lines.append("## 2. Clinical Evidence Assessment")
    lines.append("")

    # Provider verification
    pv = coverage_result.get("provider_verification", {})
    if pv and isinstance(pv, dict):
        lines.append(f"**Ordering Provider:** {pv.get('name', 'N/A')} — {pv.get('specialty', 'N/A')} — Status: {pv.get('status', 'N/A')}")
        lines.append("")

    policies = coverage_result.get("coverage_policies", [])
    if policies:
        lines.append("**Payer Policies Matched:**")
        for p in policies:
            if isinstance(p, dict):
                lines.append(f"- {p.get('policy_id', '?')}: {p.get('title', 'N/A')} ({p.get('type', '?')})")
        lines.append("")

    # Clinical evidence summary
    extraction = clinical_result.get("clinical_extraction", {})
    if isinstance(extraction, dict):
        lines.append("**Retrieved Clinical Evidence:**")
        if extraction.get("chief_complaint"):
            lines.append(f"- Chief Complaint: {extraction['chief_complaint']}")
        if extraction.get("prior_treatments"):
            lines.append(f"- Prior Treatments: {'; '.join(str(t) for t in extraction['prior_treatments'][:5])}")
        if extraction.get("severity_indicators"):
            lines.append(f"- Severity Indicators: {'; '.join(str(i) for i in extraction['severity_indicators'][:5])}")
        lines.append(f"- Extraction Confidence: {extraction.get('extraction_confidence', 0)}%")
        lines.append("")

    # --- Section 3: Criterion-by-Criterion Evaluation ---
    lines.append("## 3. Criterion-by-Criterion Evaluation")
    lines.append("")

    criteria = coverage_result.get("criteria_assessment", [])
    if criteria:
        lines.append(f"**Payer Requirements Met:** {audit_trail.get('criteria_met_count', '0/0')}")
        lines.append("")
        for c in criteria:
            if not isinstance(c, dict):
                continue
            status = c.get("status", "INSUFFICIENT")
            icon = {"MET": "PASS", "NOT_MET": "FAIL", "INSUFFICIENT": "INFO"}.get(status, "?")
            lines.append(f"### [{icon}] {c.get('criterion', 'N/A')}")
            lines.append(f"- **Status:** {status}")
            lines.append(f"- **Confidence:** {c.get('confidence', 0)}%")
            evidence = c.get("evidence", [])
            if isinstance(evidence, list) and evidence:
                lines.append("- **Evidence:**")
                for e in evidence:
                    lines.append(f"  - {str(e)}")
            elif isinstance(evidence, str) and evidence:
                lines.append(f"- **Evidence:** {evidence}")
            if c.get("notes"):
                lines.append(f"- **Notes:** {c['notes']}")
            lines.append("")
    else:
        lines.append("No payer policy requirements were identified for evaluation.")
        lines.append("")

    # --- Section 4: Validation Checks ---
    lines.append("## 4. Validation Checks")
    lines.append("")

    # Provider verification
    if pv and isinstance(pv, dict):
        lines.append(f"**Provider Credentials:** NPI {pv.get('npi', 'N/A')} — {pv.get('status', 'N/A')}")
        if pv.get("detail"):
            lines.append(f"  Detail: {pv['detail']}")
        lines.append("")

    # Diagnosis code validation
    dx_val = clinical_result.get("diagnosis_validation", [])
    if dx_val:
        lines.append("**Diagnosis Code Validation:**")
        lines.append("")
        lines.append("| Code | Description | Billable | Valid |")
        lines.append("|------|-------------|----------|------|")
        for d in dx_val:
            if isinstance(d, dict):
                code = d.get("code", "?")
                desc = d.get("description", "N/A")[:60]
                billable = "Yes" if d.get("billable") else "No"
                valid = "Yes" if d.get("valid") else "No"
                lines.append(f"| {code} | {desc} | {billable} | {valid} |")
        lines.append("")

    # Compliance checklist
    checklist = compliance_result.get("checklist", [])
    if checklist:
        lines.append("**Documentation Completeness Checklist:**")
        lines.append("")
        lines.append("| Item | Status | Detail |")
        lines.append("|------|--------|--------|")
        for item in checklist:
            if isinstance(item, dict):
                lines.append(f"| {item.get('item', '?')} | {item.get('status', '?')} | {item.get('detail', '')[:60]} |")
        lines.append("")

    # --- Section 5: Submission Readiness Rationale ---
    lines.append("## 5. Submission Readiness Rationale")
    lines.append("")
    lines.append(f"**Status:** {recommendation_display}")
    # Render decision gates — field may contain pipe-separated gates
    gate_raw = synthesis.get("decision_gate", "N/A")
    gate_parts = [g.strip() for g in str(gate_raw).split("|") if g.strip()]
    if len(gate_parts) > 1:
        lines.append("")
        lines.append("**Readiness Gates:**")
        for gp in gate_parts:
            # Extract gate label (e.g. "GATE 1 (Provider)") and result
            if ": PASS" in gp.upper():
                lines.append(f"- [PASS] {gp}")
            elif ": FAIL" in gp.upper():
                lines.append(f"- [FAIL] {gp}")
            else:
                lines.append(f"- {gp}")
    else:
        lines.append(f"**Gate:** {gate_raw}")
    lines.append(f"**Confidence:** {confidence_level} ({int(confidence * 100)}%)")
    lines.append("")
    lines.append(synthesis.get("clinical_rationale", "No rationale provided."))
    lines.append("")

    # Supporting facts
    met_criteria = synthesis.get("coverage_criteria_met", [])
    if met_criteria:
        lines.append("**Requirements Met — Key Evidence:**")
        for m in met_criteria:
            lines.append(f"- {str(m)}")
        lines.append("")

    # --- Section 6: Documentation Gaps ---
    gaps = coverage_result.get("documentation_gaps", [])
    if gaps:
        lines.append("## 6. Documentation Gaps")
        lines.append("")
        for g in gaps:
            if isinstance(g, dict):
                critical = "CRITICAL" if g.get("critical") else "Non-critical"
                lines.append(f"- [{critical}] {g.get('what', g.get('description', 'N/A'))}")
                if g.get("request"):
                    lines.append(f"  Action Required: {g['request']}")
            else:
                lines.append(f"- {str(g)}")
        lines.append("")

    # --- Section 7: Audit Trail ---
    lines.append("## 7. Audit Trail")
    lines.append("")
    lines.append("**Data Sources Consulted:**")
    for src in audit_trail.get("data_sources", []):
        lines.append(f"- {src}")
    lines.append("")
    lines.append(f"- Assessment Started: {audit_trail.get('review_started', 'N/A')}")
    lines.append(f"- Assessment Completed: {audit_trail.get('review_completed', 'N/A')}")
    lines.append(f"- Evidence Extraction Confidence: {audit_trail.get('extraction_confidence', 0)}%")
    lines.append(f"- Policy Matching Confidence: {audit_trail.get('assessment_confidence', 0)}%")
    lines.append(f"- Requirements Met: {audit_trail.get('criteria_met_count', '0/0')}")
    lines.append("")

    # --- Section 8: Compliance Notes ---
    lines.append("## 8. Compliance Notes")
    lines.append("")
    lines.append("**Assessment Policy:** Provider Submission Readiness Mode")
    lines.append("- Provider credential check: Required before submission")
    lines.append("- Code validation: Required — coding errors cause avoidable denials")
    lines.append("- Payer policy requirements: All must be MET for ready-to-submit status")
    lines.append("- Unmet/insufficient requirements: Flagged as needs-review (not a denial)")
    lines.append("- Human review required: Before final submission to payer")
    lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Generated: {now} | AI-Assisted Provider Prior Authorization System*")

    return "\n".join(lines)


def _build_trace_events(phases: list[dict], request_data: dict) -> list[dict]:
    """Flatten the trace into an ordered, navigable event list for the Debug
    Console's ADK-style Event inspector (user_input → llm/tool steps → final).
    Cross-agent offsets are per-agent-relative, so events keep phase→agent→step
    insertion order rather than a global time sort. No PHI: the user_input event
    shows only non-PHI request fields; tool payloads are already redacted."""
    events: list[dict] = []
    eid = 0
    req_summary = {
        "diagnosis_codes": request_data.get("diagnosis_codes", []),
        "procedure_codes": request_data.get("procedure_codes", []),
        "provider_npi": request_data.get("provider_npi", ""),
        "payer_name": request_data.get("payer_name", ""),
    }
    events.append({
        "id": eid, "type": "user_input", "phase": "", "agent": "",
        "label": "User request", "status": "done", "duration_ms": 0,
        "started_offset_ms": 0, "request": json.dumps(req_summary, indent=2),
        "response": "",
    })
    eid += 1
    for ph in phases:
        for ag in ph.get("agents", []):
            for st in ag.get("steps", []):
                if st.get("kind") == "llm":
                    events.append({
                        "id": eid, "type": "llm_call", "phase": ph.get("name", ""),
                        "agent": ag.get("name", ""),
                        "label": f"{ag.get('name', '')} · model.call ({st.get('model', '')})",
                        "status": st.get("status", "done"),
                        "duration_ms": st.get("duration_ms", 0),
                        "started_offset_ms": st.get("started_offset_ms", 0),
                        "request": f"input_tokens: {st.get('input_tokens', 0)}",
                        "response": f"output_tokens: {st.get('output_tokens', 0)}",
                    })
                else:
                    events.append({
                        "id": eid, "type": "tool_call", "phase": ph.get("name", ""),
                        "agent": ag.get("name", ""),
                        "label": f"{ag.get('name', '')} · {st.get('name', '')}",
                        "status": st.get("status", "pass"),
                        "duration_ms": st.get("duration_ms", 0),
                        "started_offset_ms": st.get("started_offset_ms", 0),
                        "request": st.get("args_full", ""),
                        "response": st.get("result_full", ""),
                    })
                eid += 1
    events.append({
        "id": eid, "type": "final", "phase": "phase_4", "agent": "synthesis",
        "label": "Final recommendation", "status": "done", "duration_ms": 0,
        "started_offset_ms": phases[-1].get("started_offset_ms", 0) if phases else 0,
        "request": "", "response": "",
    })
    return events


async def run_multi_agent_review(
    request_data: dict,
    on_progress: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """Run the multi-agent prior auth preparation pipeline.

    Phase 1 (parallel): Documentation Completeness + Clinical Evidence Retrieval
    Phase 2 (sequential): Policy Matching Agent (receives clinical findings)
    Phase 3: Submission Readiness Agent reads all reports, produces submission assessment
    Phase 4: Audit trail assembly and justification document generation

    Args:
        request_data: Dict with patient_name, patient_dob, provider_npi,
            diagnosis_codes, procedure_codes, clinical_notes, insurance_id.
        on_progress: Optional async callback for streaming progress events.

    Returns:
        Dict with recommendation, confidence, confidence_level, summary,
        tool_results, clinical_rationale, coverage criteria,
        policy_references, disclaimer, agent_results, audit_trail,
        and audit_justification (markdown string).
    """
    request_id = request_data.get("request_id", "unknown")

    with tracer.start_as_current_span("prior_auth_preparation") as root_span:
        root_span.set_attribute("request_id", request_id)
        return await _run_review_pipeline(request_data, on_progress, root_span)


async def _run_review_pipeline(
    request_data: dict,
    on_progress: Callable[[dict], Awaitable[None]] | None,
    root_span,
) -> dict:
    """Inner pipeline — extracted so the top-level span wraps everything."""
    start_time = datetime.now(timezone.utc).isoformat()
    request_id = request_data.get("request_id", "unknown")

    # --- Execution-trace scaffolding (in-app technical-demo timeline) ---
    pipeline_start = time.monotonic()
    model_name = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.4")
    trace_phases: list[dict] = []

    def _off() -> int:
        return int((time.monotonic() - pipeline_start) * 1000)

    async def _timed(agent_name: str, fn, *args):
        """Run an agent via _safe_run and return (result, duration_ms)."""
        _t0 = time.monotonic()
        result = await _safe_run(agent_name, fn, *args)
        return result, int((time.monotonic() - _t0) * 1000)

    def _trace_agent(name: str, result: dict, duration_ms: int, model: str = "") -> dict:
        if _uses_agent_fallback(result):
            status = "warning"
        elif result.get("error"):
            status = "error"
        else:
            status = "done"
        calls: list[dict] = []
        for tr in (result.get("tool_results") or []):
            if not isinstance(tr, dict):
                continue
            raw = str(tr.get("status", "")).lower()
            calls.append({
                "tool_name": tr.get("tool_name", ""),
                "server_label": tr.get("server_label", ""),
                "tool": tr.get("tool") or tr.get("tool_name", ""),
                "status": "fail" if raw in ("fail", "error", "failed") else "pass",
                "order": int(tr.get("order", 0) or 0),
                "duration_ms": int(tr.get("duration_ms", 0) or 0),
                "started_offset_ms": int(tr.get("started_offset_ms", 0) or 0),
                "args_summary": str(tr.get("args_summary", "")),
                "result_summary": str(tr.get("result_summary", "")),
                "args_full": str(tr.get("args_full", "") or tr.get("args_summary", "")),
                "result_full": str(tr.get("result_full", "") or tr.get("result_summary", "")),
            })
        calls.sort(key=lambda c: c["order"])

        # Unified ordered steps: model calls + tool calls interleaved by offset.
        steps: list[dict] = []
        for m in (result.get("model_calls") or []):
            if not isinstance(m, dict):
                continue
            steps.append({
                "kind": "llm", "name": "model.call", "status": "done",
                "model": str(m.get("model", "") or model or model_name),
                "duration_ms": int(m.get("duration_ms", 0) or 0),
                "started_offset_ms": int(m.get("started_offset_ms", 0) or 0),
                "input_tokens": int(m.get("input_tokens", 0) or 0),
                "output_tokens": int(m.get("output_tokens", 0) or 0),
            })
        for c in calls:
            steps.append({
                "kind": "tool", "name": c["tool"], "status": c["status"],
                "server_label": c["server_label"], "duration_ms": c["duration_ms"],
                "started_offset_ms": c["started_offset_ms"],
                "args_full": c["args_full"], "result_full": c["result_full"],
            })
        steps.sort(key=lambda s: s.get("started_offset_ms", 0))

        return {
            "name": name, "status": status, "duration_ms": duration_ms,
            "model": model or model_name, "tool_calls": calls, "steps": steps,
            "response_id": str(result.get("_foundry_response_id", "") or ""),
            "session_id": str(result.get("_foundry_session_id", "") or ""),
        }

    async def _emit(event: dict) -> None:
        if on_progress:
            await on_progress(event)

    async def _emit_trace() -> None:
        await _emit({"trace": {
            "request_id": request_id, "started_at": start_time, "completed_at": "",
            "total_duration_ms": _off(), "phases": trace_phases,
        }})

    # --- Pre-flight: CPT/HCPCS format validation ---
    logger.info("Pre-flight: Validating procedure code formats")
    _pf_start = _off()
    _pf_t0 = time.monotonic()
    cpt_validation = validate_procedure_codes(
        request_data.get("procedure_codes", [])
    )
    _pf_ms = int((time.monotonic() - _pf_t0) * 1000)
    if not cpt_validation["valid"]:
        logger.warning("CPT validation found invalid codes: %s", cpt_validation["summary"])

    trace_phases.append({
        "name": "preflight", "status": "completed",
        "started_offset_ms": _pf_start, "duration_ms": _pf_ms,
        "agents": [{
            "name": "CPT/HCPCS Format Validation", "status": "done",
            "duration_ms": _pf_ms, "model": "local",
            "tool_calls": [{
                "tool_name": "cpt_format_validation", "tool": "cpt_format_validation",
                "server_label": "local",
                "status": "pass" if cpt_validation["valid"] else "fail",
                "order": 0, "duration_ms": _pf_ms, "started_offset_ms": 0,
                "args_summary": "", "result_summary": cpt_validation["summary"][:500],
            }],
        }],
    })

    await _emit({
        "phase": "preflight", "status": "completed", "progress_pct": 5,
        "message": "CPT/HCPCS format validation complete",
        "agents": {},
    })
    await _emit_trace()

    # --- Phase 1: Parallel — Documentation Completeness + Clinical Evidence Retrieval ---
    logger.info("Phase 1: Running Documentation Completeness and Clinical Evidence agents in parallel")

    # Inject CPT pre-flight results into request data so the clinical agent
    # can reference them in procedure_validation (source: "orchestrator_preflight")
    clinical_request = {**request_data, "cpt_preflight": cpt_validation}

    await _emit({
        "phase": "phase_1", "status": "running", "progress_pct": 10,
        "message": "Running Documentation Completeness and Clinical Evidence agents in parallel",
        "agents": {
            "compliance": {"status": "running", "detail": "Checking documentation completeness for submission"},
            "clinical": {"status": "running", "detail": "Validating codes and retrieving clinical evidence"},
        },
    })

    _p1_start = _off()
    with tracer.start_as_current_span("phase_1_parallel") as p1_span:
        compliance_task = asyncio.create_task(
            _timed("Compliance Agent", run_compliance_review, request_data)
        )
        clinical_task = asyncio.create_task(
            _timed("Clinical Reviewer Agent", run_clinical_review, clinical_request)
        )

        (compliance_result, _comp_ms), (clinical_result, _clin_ms) = await asyncio.gather(
            compliance_task, clinical_task
        )

        p1_span.set_attribute("agent.compliance.status",
                              "error" if compliance_result.get("error") else "success")
        p1_span.set_attribute("agent.clinical.status",
                              "error" if clinical_result.get("error") else "success")

    if _is_hosted_agent_error(clinical_result):
        logger.warning(
            "Clinical hosted agent failed; using conservative fallback: %s",
            clinical_result.get("error"),
        )
        clinical_result = _build_clinical_fallback_result(
            clinical_request,
            cpt_validation,
            clinical_result.get("error", ""),
        )

    # Build per-agent status with validation warnings
    def _agent_status(name: str, result: dict, ok_msg: str) -> dict:
        if _uses_agent_fallback(result):
            return {"status": "warning", "detail": result["_fallback_reason"]}
        if result.get("error"):
            return {"status": "error", "detail": result["error"]}
        missing = _validate_agent_result(name, result)
        if missing:
            return {
                "status": "warning",
                "detail": f"Partial result — missing: {', '.join(missing)}",
            }
        return {"status": "done", "detail": ok_msg}

    await _emit({
        "phase": "phase_1", "status": "completed", "progress_pct": 40,
        "message": "Compliance and Clinical agents completed",
        "agents": {
            "compliance": _agent_status(
                "Compliance Agent", compliance_result,
                "Documentation completeness check complete",
            ),
            "clinical": _agent_status(
                "Clinical Reviewer Agent", clinical_result,
                "Clinical evidence retrieval complete",
            ),
        },
    })

    trace_phases.append({
        "name": "phase_1", "status": "completed",
        "started_offset_ms": _p1_start, "duration_ms": _off() - _p1_start,
        "agents": [
            _trace_agent("Documentation Completeness", compliance_result, _comp_ms),
            _trace_agent("Clinical Evidence Retrieval", clinical_result, _clin_ms),
        ],
    })
    await _emit_trace()

    # --- Phase 2: Sequential — Policy Matching Agent (needs clinical findings) ---
    logger.info("Phase 2: Running Policy Matching Agent with clinical findings")

    await _emit({
        "phase": "phase_2", "status": "running", "progress_pct": 45,
        "message": "Running Policy Matching Agent with clinical findings",
        "agents": {
            "coverage": {"status": "running", "detail": "Verifying provider credentials and matching payer policy requirements"},
        },
    })

    _p2_start = _off()
    with tracer.start_as_current_span("phase_2_coverage") as p2_span:
        coverage_result, _cov_ms = await _timed(
            "Coverage Agent", run_coverage_review, request_data, clinical_result
        )

        if _is_hosted_agent_error(coverage_result):
            logger.warning(
                "Coverage hosted agent failed; using conservative fallback: %s",
                coverage_result.get("error"),
            )
            coverage_result = _build_coverage_fallback_result(
                request_data,
                clinical_result,
                coverage_result.get("error", ""),
            )

        # Normalize coverage result (fix provider data format, etc.)
        coverage_result = _normalize_coverage_result(coverage_result)

        # Deterministically fill provider taxonomies + per-code coverage matrix
        # from the medical-data MCP server (removes LLM run-to-run variance for
        # these demo-critical fields). Best-effort; no-op on failure.
        coverage_result = await enrich_coverage(coverage_result, request_data)

        p2_span.set_attribute("agent.coverage.status",
                              "error" if coverage_result.get("error") else "success")

    await _emit({
        "phase": "phase_2", "status": "completed", "progress_pct": 70,
        "message": "Policy Matching Agent completed",
        "agents": {
            "coverage": _agent_status(
                "Coverage Agent", coverage_result,
                "Payer policy matching complete",
            ),
        },
    })

    trace_phases.append({
        "name": "phase_2", "status": "completed",
        "started_offset_ms": _p2_start, "duration_ms": _off() - _p2_start,
        "agents": [_trace_agent("Policy Matching", coverage_result, _cov_ms)],
    })
    await _emit_trace()

    # --- Phase 3: Submission Readiness Assessment ---
    logger.info("Phase 3: Assessing submission readiness")

    await _emit({
        "phase": "phase_3", "status": "running", "progress_pct": 75,
        "message": "Assessing prior auth submission readiness",
        "agents": {
            "synthesis": {"status": "running", "detail": "Applying submission readiness gates"},
        },
    })

    _p3_start = _off()
    _p3_t0 = time.monotonic()
    with tracer.start_as_current_span("phase_3_synthesis") as p3_span:
        synthesis = await _run_synthesis(
            request_data, compliance_result, clinical_result, coverage_result,
            cpt_validation,
        )

        synthesis = _apply_fallback_synthesis_guardrails(
            synthesis,
            clinical_result,
            coverage_result,
        )
        _syn_ms = int((time.monotonic() - _p3_t0) * 1000)

        p3_span.set_attribute("synthesis.recommendation",
                              synthesis.get("recommendation", "unknown"))
        p3_span.set_attribute("synthesis.confidence",
                              synthesis.get("confidence", 0.0))

    # Coerce list[str] fields from synthesis — agent may return list[dict]
    for _str_list_key in (
        "coverage_criteria_met", "coverage_criteria_not_met",
        "missing_documentation", "policy_references",
    ):
        val = synthesis.get(_str_list_key)
        if isinstance(val, list):
            synthesis[_str_list_key] = [
                str(item) if not isinstance(item, str) else item
                for item in val
            ]

    # synthesis_audit_trail comes as a JSON-encoded string from the agent
    # (Responses API structured output doesn't support unconstrained dict).
    # Parse it back to dict for the backend/frontend API contract.
    _sat = synthesis.get("synthesis_audit_trail")
    if isinstance(_sat, str) and _sat:
        try:
            synthesis["synthesis_audit_trail"] = json.loads(_sat)
        except (json.JSONDecodeError, TypeError):
            synthesis["synthesis_audit_trail"] = {}

    await _emit({
        "phase": "phase_3", "status": "completed", "progress_pct": 90,
        "message": "Submission readiness assessment complete",
        "agents": {
            "synthesis": {"status": "done", "detail": "Submission readiness gates applied"},
        },
    })

    trace_phases.append({
        "name": "phase_3", "status": "completed",
        "started_offset_ms": _p3_start, "duration_ms": _syn_ms,
        "agents": [_trace_agent("Submission Readiness", synthesis, _syn_ms)],
    })
    await _emit_trace()

    # --- Phase 4: Audit Trail & Justification ---
    logger.info("Phase 4: Building audit trail and justification document")

    await _emit({
        "phase": "phase_4", "status": "running", "progress_pct": 92,
        "message": "Building audit trail and justification document",
        "agents": {},
    })

    _p4_start = _off()
    _p4_t0 = time.monotonic()
    with tracer.start_as_current_span("phase_4_audit") as p4_span:
        confidence, confidence_level = _compute_confidence(
            compliance_result, clinical_result, coverage_result
        )

        # Use synthesis confidence if available, fall back to computed
        final_confidence = synthesis.get("confidence", confidence)
        final_level = synthesis.get("confidence_level", confidence_level)

        audit_trail = _build_audit_trail(
            compliance_result, clinical_result, coverage_result, start_time,
            synthesis=synthesis,
        )

        audit_justification = _generate_audit_justification(
            request_data, synthesis,
            compliance_result, clinical_result, coverage_result,
            audit_trail,
        )

        audit_justification_pdf = generate_audit_justification_pdf(
            request_data, synthesis,
            compliance_result, clinical_result, coverage_result,
            audit_trail,
        )

        p4_span.set_attribute("audit.confidence", final_confidence)
        p4_span.set_attribute("audit.confidence_level", final_level)

    _p4_ms = int((time.monotonic() - _p4_t0) * 1000)
    trace_phases.append({
        "name": "phase_4", "status": "completed",
        "started_offset_ms": _p4_start, "duration_ms": _p4_ms,
        "agents": [{
            "name": "Audit Trail & Justification", "status": "done",
            "duration_ms": _p4_ms, "model": "local", "tool_calls": [],
        }],
    })
    execution_trace = {
        "request_id": request_id, "started_at": start_time,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "total_duration_ms": _off(), "phases": trace_phases,
        "events": _build_trace_events(trace_phases, request_data),
    }

    # --- Assemble final response ---
    all_tool_results = []

    # Add CPT validation as a tool result
    all_tool_results.append({
        "tool_name": "cpt_format_validation",
        "status": "pass" if cpt_validation["valid"] else "fail",
        "detail": cpt_validation["summary"],
    })

    # Collect agent-reported tool_results and normalize status values.
    # Agents (LLMs) may use "success"/"error" instead of the frontend's
    # expected "pass"/"fail"/"warning" vocabulary.
    _STATUS_MAP = {
        "success": "pass",
        "completed": "pass",
        "found": "pass",
        "verified": "pass",
        "valid": "pass",
        "error": "fail",
        "failed": "fail",
        "invalid": "fail",
        "not_found": "warning",
        "partial": "warning",
        "info": "warning",
    }

    def _normalize_tool_result(tr: dict) -> dict:
        raw = str(tr.get("status", "warning")).lower().strip()
        return {
            "tool_name": tr.get("tool_name", "unknown"),
            "status": _STATUS_MAP.get(raw, raw),  # map or keep as-is
            "detail": tr.get("detail", ""),
        }

    for tr in clinical_result.get("tool_results", []):
        if isinstance(tr, dict) and not _is_fallback_tool_result(tr):
            all_tool_results.append(_normalize_tool_result(tr))

    for tr in coverage_result.get("tool_results", []):
        if isinstance(tr, dict) and not _is_fallback_tool_result(tr):
            all_tool_results.append(_normalize_tool_result(tr))

    # If agents didn't report tool_results, synthesize from available data
    existing_tools = {t.get("tool_name", "") for t in all_tool_results}

    # ICD-10 validation from clinical agent
    dx_val = clinical_result.get("diagnosis_validation", [])
    if (
        dx_val
        and not _uses_agent_fallback(clinical_result)
        and not any("icd" in t.lower() or "diagnosis" in t.lower() for t in existing_tools)
    ):
        valid_count = sum(1 for d in dx_val if isinstance(d, dict) and d.get("valid"))
        billable_count = sum(1 for d in dx_val if isinstance(d, dict) and d.get("billable"))
        total = len(dx_val)
        all_tool_results.append({
            "tool_name": "icd10_validation",
            "status": "pass" if valid_count == total else "warning",
            "detail": f"{valid_count}/{total} codes valid, {billable_count}/{total} billable",
        })

    # NPI verification from coverage agent
    pv = coverage_result.get("provider_verification", {})
    if (
        pv
        and isinstance(pv, dict)
        and pv.get("npi")
        and not _uses_agent_fallback(coverage_result)
        and not any("npi" in t.lower() for t in existing_tools)
    ):
        pv_status = pv.get("status", "unknown").upper()
        all_tool_results.append({
            "tool_name": "npi_verification",
            "status": "pass" if pv_status in ("VERIFIED", "ACTIVE") else "warning",
            "detail": f"NPI {pv.get('npi')} — {pv.get('name', 'N/A')} — {pv_status}",
        })

    # Coverage policy search from coverage agent
    policies = coverage_result.get("coverage_policies", [])
    if policies and not any("coverage" in t.lower() or "cms" in t.lower() for t in existing_tools):
        all_tool_results.append({
            "tool_name": "cms_coverage_search",
            "status": "pass",
            "detail": f"{len(policies)} coverage policies found",
        })

    await _emit({
        "phase": "phase_4", "status": "completed", "progress_pct": 100,
        "message": "Prior auth preparation complete",
        "agents": {},
    })

    # Normalize legacy recommendation values from synthesis agent
    recommendation = _normalize_recommendation_value(
        synthesis.get("recommendation", "needs_review")
    )
    synthesis["recommendation"] = recommendation

    return {
        **synthesis,
        "confidence": final_confidence,
        "confidence_level": final_level,
        "tool_results": all_tool_results,
        "agent_results": {
            "compliance": _enrich_agent_result("compliance", compliance_result),
            "clinical": _enrich_agent_result("clinical", clinical_result),
            "coverage": _enrich_agent_result("coverage", coverage_result),
        },
        "audit_trail": audit_trail,
        "execution_trace": execution_trace,
        "audit_justification": audit_justification,
        "audit_justification_pdf": audit_justification_pdf,
    }


async def _safe_run(agent_name: str, fn, *args) -> dict:
    """Run an agent function with error handling and automatic retry.

    On Windows, if the running event loop is a SelectorEventLoop (which
    uvicorn --reload may create), subprocess execution will fail. In that
    case, run the agent in a separate thread with its own ProactorEventLoop.

    After each attempt, the result is validated against ``_EXPECTED_KEYS``.
    If required keys are missing (e.g. from a truncated API response), the
    agent is retried up to ``_MAX_AGENT_RETRIES`` times.

    Returns the agent's result dict on success, or an error dict on failure.
    """
    last_result: dict = {"error": "Agent did not run", "tool_results": []}

    for attempt in range(_MAX_AGENT_RETRIES + 1):
        try:
            loop = asyncio.get_running_loop()
            loop_type = type(loop).__name__
            policy_type = type(asyncio.get_event_loop_policy()).__name__
            logger.info(
                "[debug] %s (attempt %d/%d) — event loop: %s, policy: %s",
                agent_name, attempt + 1, _MAX_AGENT_RETRIES + 1,
                loop_type, policy_type,
            )

            # If running on a SelectorEventLoop, subprocess_exec won't work.
            # Run the agent in a separate thread with a ProactorEventLoop.
            if isinstance(loop, asyncio.SelectorEventLoop) and os.name == "nt":
                logger.info("[debug] %s — SelectorEventLoop detected, using thread-based ProactorEventLoop", agent_name)
                last_result = await _run_in_proactor_thread(agent_name, fn, *args)
            else:
                last_result = await fn(*args)

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error("%s attempt %d failed:\n%s", agent_name, attempt + 1, tb)
            last_result = {"error": str(e), "tool_results": []}

        # Validate result completeness
        missing = _validate_agent_result(agent_name, last_result)
        if not missing:
            if attempt > 0:
                logger.info(
                    "%s succeeded on retry (attempt %d/%d)",
                    agent_name, attempt + 1, _MAX_AGENT_RETRIES + 1,
                )
            return last_result

        # Result is incomplete — decide whether to retry
        if attempt < _MAX_AGENT_RETRIES:
            logger.warning(
                "%s returned incomplete result (attempt %d/%d). "
                "Missing keys: %s. Retrying...",
                agent_name, attempt + 1, _MAX_AGENT_RETRIES + 1,
                ", ".join(missing),
            )
        else:
            logger.error(
                "%s returned incomplete result after %d attempt(s). "
                "Missing keys: %s. Using partial result.",
                agent_name, attempt + 1, ", ".join(missing),
            )

    return last_result


async def _run_in_proactor_thread(agent_name: str, fn, *args) -> dict:
    """Run an async agent function in a separate thread with ProactorEventLoop.

    This is the workaround for Windows uvicorn --reload creating a
    SelectorEventLoop which doesn't support subprocess creation.
    """
    import concurrent.futures

    def _thread_target():
        """Create a ProactorEventLoop in this thread and run the agent."""
        proactor_loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(proactor_loop)
        try:
            return proactor_loop.run_until_complete(fn(*args))
        finally:
            proactor_loop.close()

    # Run in a thread pool to avoid blocking the main event loop
    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix=f"agent-{agent_name}"
    ) as executor:
        result = await loop.run_in_executor(executor, _thread_target)

    return result


async def _run_synthesis(
    request_data: dict,
    compliance_result: dict,
    clinical_result: dict,
    coverage_result: dict,
    cpt_validation: dict | None = None,
) -> dict:
    """Delegate to the synthesis_agent dispatcher (mirrors clinical/compliance/coverage pattern)."""
    return await _dispatch_synthesis(
        request_data=request_data,
        compliance_result=compliance_result,
        clinical_result=clinical_result,
        coverage_result=coverage_result,
        cpt_validation=cpt_validation,
    )
