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

router = APIRouter()


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
