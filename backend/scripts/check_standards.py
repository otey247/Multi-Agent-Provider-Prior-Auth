"""Standalone behavioral check for the standards layer (no pytest, no agents).

Run from backend/:
    python scripts/check_standards.py

Exercises the deterministic CRD/DTR/PAS assessment + demo-NPI fix against the
real orthopedics sample-case data. Exits non-zero on failure.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.policy_store import match_policy_pack  # noqa: E402
from app.services.standards import (  # noqa: E402
    apply_demo_provider_verification,
    build_standards_assessment,
)

# Orthopedics sample case (frontend/lib/sample-case.ts -> orthopedic-surgery),
# trimmed clinical_notes preserving the phrases the evaluator keys on.
ORTHO_REQUEST = {
    "patient_name": "Thomas Reed",
    "provider_npi": "1669542008",
    "ordering_provider_name": "Meghan Osei, MD",
    "rendering_provider_specialty": "Orthopedic Spine Surgery",
    "servicing_facility": "Summit Ambulatory Surgery Center",
    "payer_name": "UnitedHealthcare",
    "payer_plan": "Commercial HMO",
    "diagnosis_codes": ["M43.16", "M54.16", "M48.062"],
    "procedure_codes": ["22612", "22840"],
    "clinical_notes": (
        "59-year-old male with 14 months of refractory low back pain. MRI lumbar "
        "spine shows grade 1 degenerative spondylolisthesis at L4-L5 with severe "
        "central canal stenosis. Dynamic X-rays demonstrate instability with 4 mm "
        "translation on flexion-extension views. Oswestry Disability Index 46%. "
        "Positive straight leg raise on left."
    ),
    "attached_note_types": [
        "Spine surgery consult",
        "Lumbar MRI report",
        "Flexion-extension X-ray report",
        "Physical therapy summary",
    ],
    "prior_treatment_history": [
        "12 weeks of physical therapy and home exercise program",
        "Two epidural steroid injections with temporary relief",
        "Medication trial with NSAIDs and gabapentin",
    ],
}


def main() -> int:
    ok = True

    match = match_policy_pack(
        payer_name=ORTHO_REQUEST["payer_name"],
        payer_plan=ORTHO_REQUEST["payer_plan"],
        procedure_codes=ORTHO_REQUEST["procedure_codes"],
        diagnosis_codes=ORTHO_REQUEST["diagnosis_codes"],
    )

    assessment = build_standards_assessment(match, ORTHO_REQUEST, {}, {})
    crd = assessment.crd
    dtr = assessment.dtr
    pas = assessment.pas

    print("CRD determination:")
    print(f"  pa_required      = {crd.pa_required}")
    print(f"  routing_channel  = {crd.routing_channel}")
    print(f"  source           = {crd.determination_source}")

    print("\nDTR requirement evaluation:")
    for e in dtr.requirement_evaluations:
        print(f"  [{e.status:12}] {e.requirement_id:28} conf={e.confidence:3}  {e.gap_action[:50]}")
    print(f"  -> {dtr.requirements_met}/{dtr.requirements_total} MET")

    print("\nPAS preview:")
    print(f"  pas_ready          = {pas.pas_ready}")
    print(f"  submission_channel = {pas.submission_channel}")
    for m in pas.missing_for_submission:
        print(f"  missing: {m}")

    # --- assertions ---
    def check(label, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        ok = ok and cond

    print("\nAssertions:")
    check("policy pack matched", assessment.policy_pack_matched)
    check("pa_required True", crd.pa_required is True)
    check("routes to UHC payer portal", "Payer portal" in crd.routing_channel and "UnitedHealthcare" in crd.routing_channel)
    check("8 requirements total", dtr.requirements_total == 8)
    check("exactly 6 requirements MET", dtr.requirements_met == 6)
    statuses = {e.requirement_id: e.status for e in dtr.requirement_evaluations}
    check("PT discharge summary INSUFFICIENT", statuses.get("req-pt-discharge-summary") == "INSUFFICIENT")
    check("smoking cessation INSUFFICIENT", statuses.get("req-smoking-cessation") == "INSUFFICIENT")
    check("conservative therapy MET", statuses.get("req-conservative-therapy") == "MET")
    check("imaging MET (report attached)", statuses.get("req-imaging-correlation") == "MET")
    check("specialty MET", statuses.get("req-specialty-appropriateness") == "MET")
    check("PAS not ready (PT discharge gap)", pas.pas_ready is False)
    check("missing list mentions PT discharge", any("discharge" in m.lower() for m in pas.missing_for_submission))

    # --- demo NPI fix ---
    print("\nDemo-NPI fix:")
    cov = {"provider_verification": {"npi": "1669542008", "status": "not_found", "name": ""}}
    cov = apply_demo_provider_verification(cov, ORTHO_REQUEST)
    print(f"  status after fix = {cov['provider_verification']['status']}")
    check("demo NPI upgraded to VERIFIED", cov["provider_verification"]["status"] == "VERIFIED")

    cov_fb = {"_fallback_reason": "hosted down", "provider_verification": {"npi": "1669542008", "status": "UNVERIFIED"}}
    cov_fb = apply_demo_provider_verification(cov_fb, ORTHO_REQUEST)
    check("fallback result NOT upgraded", cov_fb["provider_verification"]["status"] == "UNVERIFIED")

    # --- non-matching request stays graceful ---
    nm = build_standards_assessment(
        match_policy_pack(payer_name="Aetna", payer_plan="PPO",
                          procedure_codes=["99213"], diagnosis_codes=["J18.9"]),
        {"payer_name": "Aetna"}, {}, {},
    )
    check("no-match -> runtime_search source", nm.crd.determination_source == "runtime_search")
    check("no-match -> policy_pack_matched False", nm.policy_pack_matched is False)

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
