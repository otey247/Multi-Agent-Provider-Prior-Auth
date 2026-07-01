"""CMS-0057 / Da Vinci standards-aligned domain models.

These models are the reusable "policy pack" layer described in the PRD
(items-to-implement/PRD.md, Component F). They sit *alongside* the existing
flat ``PriorAuthRequest`` and agent result schemas — they do not replace them.

A policy pack normalizes, into one reusable artifact, the answer to the
provider's two real questions for a given payer + plan + procedure + diagnosis:

  * CRD-lite  — "Is a prior auth required, and where does it route?"
                (CoverageRule + delegated_vendor on the PolicySet)
  * DTR-lite  — "Exactly what documentation does *this* payer want?"
                (DocumentationRequirement + MedicalNecessityCriterion)

Field names intentionally mirror FHIR / Da Vinci concepts (Questionnaire,
QuestionnaireResponse, Condition, Procedure, CarePlan, ...) so a future real
DTR/CRD integration can populate the same objects without a schema change.

No live payer API is called. Packs are static, human-reviewed JSON loaded
from disk by ``app.services.policy_store``.
"""

from pydantic import BaseModel


class EvidenceMapping(BaseModel):
    """Where/how the assistant should look in the chart to satisfy a requirement.

    FHIR alignment: points evidence retrieval at concrete resource types and
    paths (Condition, Observation, Procedure, CarePlan, MedicationRequest,
    DocumentReference, Encounter, ...).
    """

    mapping_id: str = ""
    requirement_id: str = ""
    fhir_resource_type: list[str] = []   # e.g. ["Procedure", "CarePlan"]
    fhir_path: str = ""
    code_system: str = ""
    value_set: str = ""
    lookback_period: str = ""            # e.g. "P6M" or "6 months"
    matching_logic: str = ""
    source_priority: int = 0
    requires_clinician_attestation: bool = False


class MedicalNecessityCriterion(BaseModel):
    """A discrete criterion that must be satisfied for medical necessity.

    DTR alignment: each criterion maps to a Questionnaire item / CQL rule. With
    no full CQL implementation (per Kevin's note), the human-readable
    ``rule_expression`` / ``human_readable_rationale`` are authoritative.
    """

    criterion_id: str = ""
    criterion_name: str = ""
    criterion_type: str = ""             # e.g. "conservative_therapy", "imaging"
    required_status: str = "required"    # "required" | "optional" | "conditional"
    rule_expression: str = ""
    human_readable_rationale: str = ""
    policy_citation: str = ""
    time_window: str = ""
    acceptable_evidence_types: list[str] = []


class DocumentationRequirement(BaseModel):
    """A required data element, document, answer, attestation, or attachment.

    DTR alignment: Questionnaire / QuestionnaireResponse. This is the canonical
    object that DTR responses, mock fixtures, payer policy docs, and manually
    extracted requirements all normalize to (PRD Component D).
    """

    requirement_id: str = ""
    requirement_type: str = ""
    description: str = ""
    required: bool = True
    conditional: bool = False
    data_element: str = ""
    fhir_resource: list[str] = []
    fhir_path: str = ""
    value_set: str = ""
    lookback_period: str = ""
    clinician_attestation_required: bool = False
    attachment_required: bool = False
    dtr_questionnaire_id: str = ""
    dtr_questionnaire_item_link_id: str = ""
    source: str = ""                     # "policy_pack" | "dtr_mock" | "crd" | ...
    source_confidence: int = 0           # 0-100


class CoverageRule(BaseModel):
    """Whether prior auth is required and under what trigger conditions (CRD)."""

    rule_id: str = ""
    rule_type: str = ""                  # "pa_required" | "pa_conditional" | ...
    trigger_procedure_codes: list[str] = []
    trigger_diagnosis_codes: list[str] = []
    trigger_place_of_service: list[str] = []
    trigger_provider_specialty: list[str] = []
    trigger_line_of_business: list[str] = []
    pa_required: bool = True
    rule_expression: str = ""
    source_evidence: str = ""
    confidence: int = 0                  # 0-100


class PolicySet(BaseModel):
    """A reusable payer/plan/procedure/diagnosis policy pack.

    This is the central artifact of the standards layer. One pack drives the
    CRD-lite determination, the DTR-lite requirement checklist, and the
    evidence-mapping hints for the runtime agents.
    """

    policy_set_id: str = ""
    payer: str = ""
    plan: str = ""
    line_of_business: str = ""
    delegated_vendor: str = ""           # e.g. "eviCore by Evernorth"; "" if none
    policy_name: str = ""
    policy_version: str = ""
    effective_date: str = ""
    expiration_date: str = ""
    source_type: str = ""                # "payer_policy" | "delegated_um_guideline"
    source_url: str = ""
    procedure_category: str = ""
    procedure_codes: list[str] = []
    diagnosis_codes: list[str] = []
    coverage_rules: list[CoverageRule] = []
    documentation_requirements: list[DocumentationRequirement] = []
    medical_necessity_criteria: list[MedicalNecessityCriterion] = []
    evidence_mappings: list[EvidenceMapping] = []
    confidence: int = 0                  # 0-100 (human-reviewed pack quality)
    last_reviewed_at: str = ""
    reviewed_by: str = ""


class PolicyPackMatch(BaseModel):
    """Result of matching a request against the available policy packs."""

    matched: bool = False
    policy_set_id: str = ""
    payer: str = ""
    plan: str = ""
    delegated_vendor: str = ""
    pa_required: bool | None = None
    confidence: float = 0.0              # 0.0-1.0 match strength
    reasons: list[str] = []              # why this pack matched (audit trail)
    policy_set: PolicySet | None = None


# --- Runtime assessment surfaced in the API response (PRD Components C/D/E) ---
# These are the standards-aligned outputs the review pipeline computes
# deterministically from a matched policy pack + the existing agent results.
# No live payer API is called; everything is derived locally.


class CrdDetermination(BaseModel):
    """CRD-lite: is prior auth required, and where does it route?"""

    pa_required: bool | None = None
    routing_channel: str = ""            # "Payer portal (...)" | "Delegated UM vendor: ..."
    delegated_vendor: str = ""
    determination_source: str = ""       # "policy_pack" | "runtime_search" | "unknown"
    reasons: list[str] = []


class RequirementEvaluation(BaseModel):
    """DTR-lite: one payer requirement evaluated against the chart/packet."""

    requirement_id: str = ""
    description: str = ""
    requirement_type: str = ""
    required: bool = True
    conditional: bool = False
    status: str = "MISSING"              # "MET" | "INSUFFICIENT" | "MISSING" | "NOT_APPLICABLE"
    confidence: int = 0                  # 0-100
    evidence: list[str] = []
    gap_action: str = ""                 # what staff should do when not MET
    source: str = "policy_pack"


class DtrAssessment(BaseModel):
    """DTR-lite: the payer-specific requirement checklist for this request."""

    source: str = ""                     # "policy_pack" | "runtime_search"
    questionnaire_id: str = ""
    requirements_total: int = 0
    requirements_met: int = 0
    requirement_evaluations: list[RequirementEvaluation] = []


class PasPreview(BaseModel):
    """PAS-lite: a prepared package preview. Never submitted."""

    pas_ready: bool = False
    portal_ready: bool = False
    submission_channel: str = ""
    missing_for_submission: list[str] = []
    package_summary: dict = {}


class StandardsAssessment(BaseModel):
    """CMS-0057 / Da Vinci standards view attached to a ReviewResponse.

    Optional and additive — when the standards layer is disabled or no pack
    matches, ``policy_pack_matched`` is False and the runtime pipeline is
    unaffected.
    """

    enabled: bool = True
    policy_pack_matched: bool = False
    policy_set_id: str = ""
    payer: str = ""
    plan: str = ""
    policy_name: str = ""
    policy_version: str = ""
    source_url: str = ""
    crd: CrdDetermination | None = None
    dtr: DtrAssessment | None = None
    pas: PasPreview | None = None
    disclaimer: str = (
        "Standards-aligned (CMS-0057 / Da Vinci CRD/DTR/PAS) view derived from a "
        "reviewed policy pack. No live payer API was called; policy content is "
        "synthetic demo data. Human review required before submission."
    )
