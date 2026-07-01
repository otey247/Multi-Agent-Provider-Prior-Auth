"""Standards layer — deterministic CRD/DTR/PAS assessment (PRD Components C/D/E)."""

from app.services.standards.evaluator import (
    DEMO_VERIFIED_NPIS,
    apply_demo_provider_verification,
    build_standards_assessment,
)

__all__ = [
    "DEMO_VERIFIED_NPIS",
    "apply_demo_provider_verification",
    "build_standards_assessment",
]
