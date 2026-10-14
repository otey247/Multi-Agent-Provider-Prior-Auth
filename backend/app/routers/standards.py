"""Standards layer diagnostics + policy pack listing.

Fast, agent-free endpoints (PRD Component J) for confirming which CMS-0057 /
Da Vinci policy packs are loaded in a given deployment — useful to verify a
pack actually shipped in the container image without running a full review.
"""

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.policy_store import (
    get_policy_pack,
    load_policy_packs,
    policy_packs_dir,
)
from app.services.standards import (
    pas_bundle_from_review,
    questionnaire_from_pack,
    questionnaire_package_from_pack,
    questionnaire_response_from_assessment,
)

router = APIRouter()


def _require_fhir_enabled() -> None:
    if not settings.ENABLE_FHIR_ARTIFACTS:
        raise HTTPException(
            status_code=503,
            detail="FHIR artifact generation is disabled (ENABLE_FHIR_ARTIFACTS=false)",
        )


def _require_pack(policy_set_id: str):
    pack = get_policy_pack(policy_set_id)
    if not pack:
        raise HTTPException(status_code=404, detail=f"Policy pack {policy_set_id} not found")
    return pack


@router.get("/policy-packs")
async def list_policy_packs():
    """List the policy packs loaded from disk + where they were resolved from."""
    packs = load_policy_packs()
    _dir = policy_packs_dir()
    return {
        "standards_layer_enabled": settings.ENABLE_STANDARDS_LAYER,
        "policy_packs_enabled": settings.ENABLE_POLICY_PACKS,
        "packs_dir": str(_dir),
        "packs_dir_exists": _dir.is_dir(),
        "count": len(packs),
        "packs": [
            {
                "policy_set_id": p.policy_set_id,
                "payer": p.payer,
                "plan": p.plan,
                "policy_name": p.policy_name,
                "policy_version": p.policy_version,
                "delegated_vendor": p.delegated_vendor,
                "procedure_codes": p.procedure_codes,
                "diagnosis_codes": p.diagnosis_codes,
                "documentation_requirements": len(p.documentation_requirements),
                "medical_necessity_criteria": len(p.medical_necessity_criteria),
            }
            for p in packs
        ],
    }


@router.get("/policy-packs/{policy_set_id}")
async def get_policy_pack_detail(policy_set_id: str):
    """Return the full policy pack for a given id."""
    pack = get_policy_pack(policy_set_id)
    if not pack:
        raise HTTPException(status_code=404, detail=f"Policy pack {policy_set_id} not found")
    return pack.model_dump()


# --- FHIR artifacts (PRD Epic 1 — Da Vinci DTR) -----------------------------
# Synthetic demo artifacts derived from reviewed policy packs; no live payer
# API is called. Shapes live in app/services/standards/fhir.py.


@router.get("/policy-packs/{policy_set_id}/questionnaire")
async def get_pack_questionnaire(policy_set_id: str):
    """FHIR R4 Questionnaire built from the pack's documentation requirements."""
    _require_fhir_enabled()
    pack = _require_pack(policy_set_id)
    return questionnaire_from_pack(pack, canonical_base=settings.FHIR_CANONICAL_BASE)


@router.get("/policy-packs/{policy_set_id}/questionnaire-package")
async def get_pack_questionnaire_package(policy_set_id: str):
    """Parameters payload shaped like the DTR $questionnaire-package output."""
    _require_fhir_enabled()
    pack = _require_pack(policy_set_id)
    return questionnaire_package_from_pack(pack, canonical_base=settings.FHIR_CANONICAL_BASE)


def _load_review_standards(request_id: str):
    """Stored review + matched pack for FHIR artifact endpoints (404s inside)."""
    # Lazy import: keeps this router importable without the orchestrator's
    # heavier dependency stack (used by offline check scripts).
    from app.agents.orchestrator import get_review

    stored = get_review(request_id)
    if not stored:
        raise HTTPException(status_code=404, detail=f"Review {request_id} not found")
    standards = (stored.get("response") or {}).get("standards") or {}
    if not standards.get("policy_pack_matched"):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Review {request_id} has no matched policy pack — "
                "no FHIR artifacts are available"
            ),
        )
    pack = _require_pack(standards.get("policy_set_id", ""))
    return standards, pack, stored.get("request_data") or {}


@router.get("/review/{request_id}/dtr/questionnaire-response")
async def get_review_questionnaire_response(request_id: str):
    """Pre-populated QuestionnaireResponse for a completed review.

    MET requirements are answered with chart evidence (DTR information-origin
    ``auto``); unmet requirements stay unanswered and carry the gap action.
    """
    _require_fhir_enabled()
    standards, pack, request_data = _load_review_standards(request_id)
    return questionnaire_response_from_assessment(
        standards,
        pack,
        request_data,
        request_id,
        canonical_base=settings.FHIR_CANONICAL_BASE,
    )


@router.get("/review/{request_id}/pas/bundle")
async def get_review_pas_bundle(request_id: str):
    """PAS-shaped request Bundle for a completed review (export only).

    Claim (use = preauthorization) plus Patient, Coverage, Practitioner, payer
    Organization, ServiceRequest, DocumentReference stubs, and the DTR
    QuestionnaireResponse. Never submitted anywhere; a not-PAS-ready review
    exports with an explicit ``missing-for-submission`` annotation on the Claim.
    """
    _require_fhir_enabled()
    standards, pack, request_data = _load_review_standards(request_id)
    return pas_bundle_from_review(
        standards,
        pack,
        request_data,
        request_id,
        canonical_base=settings.FHIR_CANONICAL_BASE,
    )
