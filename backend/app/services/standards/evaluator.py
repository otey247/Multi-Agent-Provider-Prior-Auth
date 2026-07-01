"""Deterministic CMS-0057 / Da Vinci standards assessment.

Given a matched policy pack plus the existing agent outputs, this builds the
provider-facing CRD / DTR / PAS view *deterministically* — the same approach
``coverage_enrich`` uses to keep demo-critical fields reliable and free of
LLM run-to-run variance. No live payer API is called.

  * CRD-lite — is prior auth required, and where does it route?
  * DTR-lite — evaluate each payer requirement against the chart/packet
               (MET / INSUFFICIENT / MISSING) with evidence + a gap action.
  * PAS-lite — assemble a package-readiness preview (never submits).

It also applies the scoped demo-provider verification fix so a curated sample
case's fictional NPI does not block Gate 1 before the requirements story is
reached.
"""

from __future__ import annotations

from app.models.standards import (
    CrdDetermination,
    DtrAssessment,
    PasPreview,
    PolicyPackMatch,
    PolicySet,
    RequirementEvaluation,
    StandardsAssessment,
)

# Curated sample-case NPIs that are fictional and will not resolve in NPPES.
# Treated as verified so the requirements narrative is reachable in the demo.
# (Mirrors the agent-side demo-mode concept; extend as new sample cases land.)
DEMO_VERIFIED_NPIS: set[str] = {
    "1669542008",  # orthopedics / lumbar fusion sample case (Meghan Osei, MD)
}

_TYPE_KEYWORDS: dict[str, list[str]] = {
    "clinical_indication": [
        "month", "week", "year", "chronic", "refractory", "persistent", "duration",
    ],
    "conservative_therapy": [
        "physical therapy", "home exercise", "epidural", "injection", "nsaid",
        "gabapentin", "conservative", "analgesic", "steroid injection",
    ],
    "imaging": ["mri", "ct ", "ct scan", "x-ray", "radiograph", "imaging"],
    "instability_justification": [
        "translation", "spondylolisthesis", "instability", "listhesis", "mm ",
    ],
    "functional_impairment": [
        "oswestry", "odi", "disability index", "straight leg", "deficit",
        "claudication", "functional", "radiculopathy", "numbness",
    ],
}


def _blob(request_data: dict, clinical_result: dict) -> str:
    """Searchable lowercased text from request + clinical extraction."""
    parts: list[str] = [str(request_data.get("clinical_notes", ""))]
    for key in ("prior_treatment_history", "attached_note_types"):
        parts.extend(str(x) for x in (request_data.get(key) or []))
    ext = (clinical_result or {}).get("clinical_extraction") or {}
    if isinstance(ext, dict):
        for key in (
            "chief_complaint", "history_of_present_illness",
            "duration_and_progression", "medical_history_and_comorbidities",
        ):
            parts.append(str(ext.get(key, "")))
        for key in (
            "prior_treatments", "severity_indicators",
            "functional_limitations", "diagnostic_findings",
        ):
            parts.extend(str(x) for x in (ext.get(key) or []))
    return " \n ".join(parts).lower()


def _first_match(blob: str, keywords: list[str]) -> str:
    for kw in keywords:
        if kw.strip() and kw in blob:
            return kw.strip()
    return ""


def _specialty_match(spec: str, required: str) -> bool:
    if not spec or not required:
        return False
    if required in spec or spec in required:
        return True
    stop = {"surgery", "surgical", "medicine", "and"}
    return bool((set(spec.split()) & set(required.split())) - stop)


def _mk(
    req,
    status: str,
    confidence: int,
    evidence: list[str],
    gap_action: str,
) -> RequirementEvaluation:
    return RequirementEvaluation(
        requirement_id=req.requirement_id,
        description=req.description,
        requirement_type=req.requirement_type,
        required=req.required,
        conditional=req.conditional,
        status=status,
        confidence=confidence,
        evidence=evidence,
        gap_action=gap_action if status != "MET" else "",
        source="policy_pack",
    )


def _evaluate_requirement(
    req,
    request_data: dict,
    clinical_result: dict,
    pack: PolicySet,
) -> RequirementEvaluation:
    blob = _blob(request_data, clinical_result)
    attached = [str(a).lower() for a in (request_data.get("attached_note_types") or [])]
    rid, rtype = req.requirement_id, req.requirement_type

    # Provider specialty appropriateness — compare against the CoverageRule triggers.
    if rtype == "provider_specialty" or rid == "req-specialty-appropriateness":
        spec = str(request_data.get("rendering_provider_specialty") or "").lower()
        req_specs: set[str] = set()
        for r in pack.coverage_rules:
            req_specs |= {s.lower() for s in r.trigger_provider_specialty}
        ok = bool(spec) and (not req_specs or any(_specialty_match(spec, rs) for rs in req_specs))
        if ok:
            return _mk(req, "MET", 95,
                       [f"Rendering specialty: {request_data.get('rendering_provider_specialty')}"], "")
        return _mk(req, "INSUFFICIENT", 40, [],
                   "Confirm an in-scope specialty (orthopedic spine surgery or neurosurgery).")

    # PT discharge-summary attachment — require a *discharge* summary specifically.
    if rid == "req-pt-discharge-summary":
        if any("discharge" in a for a in attached) or "discharge summary" in blob:
            return _mk(req, "MET", 85, ["Physical therapy discharge summary attached"], "")
        if any(("physical therapy" in a) or ("therapy" in a) or a.startswith("pt") for a in attached) \
                or "physical therapy" in blob:
            return _mk(req, "INSUFFICIENT", 50,
                       ["A physical-therapy summary is present, but not a formal PT discharge summary"],
                       "Attach the formal PT discharge summary (and pain-management notes) evidencing the conservative-care trial.")
        return _mk(req, "MISSING", 20, [],
                   "Attach PT discharge summary / treatment records for the conservative-care trial.")

    # Pre-op optimization / tobacco status.
    if rtype == "preop_optimization" or rid == "req-smoking-cessation":
        if any(k in blob for k in ("smoking cessation", "tobacco cessation", "cessation counseling")):
            return _mk(req, "MET", 80, ["Smoking-cessation counseling documented"], "")
        if any(k in blob for k in ("non-smoker", "nonsmoker", "never smoker", "denies tobacco", "no tobacco")):
            return _mk(req, "MET", 75, ["Documented non-tobacco user"], "")
        return _mk(req, "INSUFFICIENT", 45, [],
                   "Confirm tobacco-use status and document smoking-cessation counseling for pre-op optimization.")

    keywords = _TYPE_KEYWORDS.get(rtype, [])
    matched = _first_match(blob, keywords) if keywords else ""

    # Imaging — also honor attachment_required.
    if rtype == "imaging":
        imaging_attached = any(
            any(tok in a for tok in ("mri", "ct", "imaging", "x-ray", "radiograph"))
            for a in attached
        )
        if matched and (imaging_attached or not req.attachment_required):
            note = "Advanced imaging documented" + (" and attached" if imaging_attached else "")
            return _mk(req, "MET", 90, [note], "")
        if matched:
            return _mk(req, "INSUFFICIENT", 55,
                       ["Imaging referenced in the note but the report is not attached"],
                       "Attach the signed MRI/CT report.")
        return _mk(req, "MISSING", 20, [],
                   "Document and attach advanced imaging (MRI/CT) correlating with symptoms.")

    # Generic keyword-driven requirement types.
    if keywords:
        if matched:
            return _mk(req, "MET", 88, [f"Documented (matched '{matched}')"], "")
        status = "MISSING" if req.required else "INSUFFICIENT"
        conf = 20 if req.required else 45
        return _mk(req, status, conf, [], req.description)

    # Unknown type — conservative default.
    return _mk(req, "INSUFFICIENT", 40, [], "Manual review required for this requirement.")


def _routing_channel(pack: PolicySet) -> str:
    if pack.delegated_vendor:
        return f"Delegated UM vendor: {pack.delegated_vendor}"
    return f"Payer portal ({pack.payer})"


def _build_pas_preview(
    pack: PolicySet,
    request_data: dict,
    evals: list[RequirementEvaluation],
    channel: str,
) -> PasPreview:
    met = sum(1 for e in evals if e.status == "MET")
    required_unmet = [e for e in evals if e.required and not e.conditional and e.status != "MET"]
    conditional_open = [e for e in evals if e.conditional and e.status not in ("MET", "NOT_APPLICABLE")]

    missing = [e.gap_action or e.description for e in required_unmet]
    missing += [f"(recommended) {e.gap_action or e.description}" for e in conditional_open]

    pas_ready = not required_unmet
    package_summary = {
        "patient": request_data.get("patient_name", ""),
        "coverage": f"{pack.payer} {pack.plan}".strip(),
        "ordering_provider": request_data.get("ordering_provider_name")
        or request_data.get("provider_npi", ""),
        "rendering_facility": request_data.get("servicing_facility", ""),
        "requested_service": ", ".join(request_data.get("procedure_codes", [])),
        "diagnoses": ", ".join(request_data.get("diagnosis_codes", [])),
        "requirements_met": f"{met}/{len(evals)}",
    }
    return PasPreview(
        pas_ready=pas_ready,
        portal_ready=pas_ready,
        submission_channel=channel,
        missing_for_submission=missing,
        package_summary=package_summary,
    )


def build_standards_assessment(
    policy_match: PolicyPackMatch | None,
    request_data: dict,
    clinical_result: dict,
    coverage_result: dict,
    *,
    enable_pas: bool = True,
) -> StandardsAssessment:
    """Build the standards-aligned (CRD/DTR/PAS) view for a review."""
    if not policy_match or not policy_match.matched or not policy_match.policy_set:
        return StandardsAssessment(
            enabled=True,
            policy_pack_matched=False,
            crd=CrdDetermination(
                pa_required=None,
                determination_source="runtime_search",
                reasons=["No payer-specific policy pack matched; runtime Medicare LCD/NCD search applies."],
            ),
            dtr=DtrAssessment(source="runtime_search"),
            pas=PasPreview(submission_channel="Manual review"),
        )

    pack = policy_match.policy_set
    channel = _routing_channel(pack)

    crd = CrdDetermination(
        pa_required=policy_match.pa_required,
        routing_channel=channel,
        delegated_vendor=pack.delegated_vendor,
        determination_source="policy_pack",
        reasons=policy_match.reasons,
    )

    evals = [
        _evaluate_requirement(req, request_data, clinical_result, pack)
        for req in pack.documentation_requirements
    ]
    met = sum(1 for e in evals if e.status == "MET")
    questionnaire_id = (
        pack.documentation_requirements[0].dtr_questionnaire_id
        if pack.documentation_requirements else ""
    )
    dtr = DtrAssessment(
        source="policy_pack",
        questionnaire_id=questionnaire_id,
        requirements_total=len(evals),
        requirements_met=met,
        requirement_evaluations=evals,
    )

    pas = _build_pas_preview(pack, request_data, evals, channel) if enable_pas else None

    return StandardsAssessment(
        enabled=True,
        policy_pack_matched=True,
        policy_set_id=pack.policy_set_id,
        payer=pack.payer,
        plan=pack.plan,
        policy_name=pack.policy_name,
        policy_version=pack.policy_version,
        source_url=pack.source_url,
        crd=crd,
        dtr=dtr,
        pas=pas,
    )


def apply_demo_provider_verification(
    coverage_result: dict,
    request_data: dict,
) -> dict:
    """Mark a curated demo NPI as verified so Gate 1 does not block the demo.

    Scoped to ``DEMO_VERIFIED_NPIS`` and skipped during coverage fallback (so a
    real hosted-agent outage still surfaces honestly as unverified).
    """
    if not isinstance(coverage_result, dict) or coverage_result.get("_fallback_reason"):
        return coverage_result

    npi = str(
        request_data.get("provider_npi")
        or request_data.get("ordering_provider_npi")
        or ""
    ).strip()
    if npi not in DEMO_VERIFIED_NPIS:
        return coverage_result

    pv = coverage_result.get("provider_verification")
    if not isinstance(pv, dict):
        pv = {}
    status = str(pv.get("status", "")).upper()
    if status in ("VERIFIED", "ACTIVE"):
        return coverage_result  # already verified — leave real data untouched

    pv["npi"] = pv.get("npi") or npi
    pv["status"] = "VERIFIED"
    pv["name"] = pv.get("name") or str(request_data.get("ordering_provider_name") or "")
    pv["specialty"] = pv.get("specialty") or str(request_data.get("rendering_provider_specialty") or "")
    pv["detail"] = (
        "Demo sample-case provider — NPPES lookup bypassed; credentials assumed "
        "verified for demonstration."
    )
    coverage_result["provider_verification"] = pv
    return coverage_result
