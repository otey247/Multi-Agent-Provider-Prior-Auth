"""FHIR R4 artifact builders for the standards layer (PRD Epics 1–2).

Renders the existing policy-pack / standards-assessment data as Da Vinci
DTR/PAS-shaped FHIR R4 resources:

  * ``questionnaire_from_pack``           — a ``Questionnaire`` whose items map
    1:1 to the pack's ``documentation_requirements``.
  * ``questionnaire_package_from_pack``   — a ``Parameters`` payload shaped
    like the DTR ``$questionnaire-package`` operation output (a collection
    ``Bundle`` holding the ``Questionnaire`` plus a ``Library`` carrying the
    pack's medical-necessity rules as human-readable text).
  * ``questionnaire_response_from_assessment`` — a ``QuestionnaireResponse``
    pre-populated from a completed review's DTR-lite evaluation: MET
    requirements are answered (with evidence and a DTR information-origin
    extension), unmet requirements stay unanswered and carry the gap action.
  * ``pas_bundle_from_review``            — a PAS request ``Bundle`` (Claim
    with ``use = preauthorization`` + Patient, Coverage, Practitioner, payer
    Organization, ServiceRequest, DocumentReference stubs, and the DTR
    QuestionnaireResponse) assembled from a completed review. Never submitted
    anywhere — it is the exportable, standards-shaped view of the packet.

Everything is built as plain dicts (no new dependencies) so the FHIR shape
lives in exactly one module. Artifacts are synthetic demo output derived from
reviewed policy packs — no live payer API is called, and the disclaimer is
stamped into every resource.
"""

from __future__ import annotations

import base64
import re
import uuid
from datetime import datetime, timezone

from app.models.standards import DocumentationRequirement, PolicySet

# Da Vinci DTR profile / extension URLs recorded on generated artifacts so a
# future validator run (or a real payer integration) knows what was intended.
DTR_QUESTIONNAIRE_PROFILE = (
    "http://hl7.org/fhir/us/davinci-dtr/StructureDefinition/dtr-std-questionnaire"
)
DTR_QUESTIONNAIRE_RESPONSE_PROFILE = (
    "http://hl7.org/fhir/us/davinci-dtr/StructureDefinition/dtr-questionnaireresponse"
)
DTR_INFORMATION_ORIGIN_EXT = (
    "http://hl7.org/fhir/us/davinci-dtr/StructureDefinition/information-origin"
)
PAS_REQUEST_BUNDLE_PROFILE = (
    "http://hl7.org/fhir/us/davinci-pas/StructureDefinition/profile-pas-request-bundle"
)
PAS_CLAIM_PROFILE = (
    "http://hl7.org/fhir/us/davinci-pas/StructureDefinition/profile-claim"
)

# Repo-scoped namespace for demo annotations with no official DTR equivalent
# (requirement evaluation status, confidence, gap action, disclaimer).
LOCAL_EXT_BASE = (
    "https://github.com/otey247/Multi-Agent-Provider-Prior-Auth"
    "/fhir/StructureDefinition"
)
LOCAL_CS_BASE = (
    "https://github.com/otey247/Multi-Agent-Provider-Prior-Auth"
    "/fhir/CodeSystem"
)

# Default canonical base stamped into Questionnaire.url / Library.url.
# Deployments override via settings.FHIR_CANONICAL_BASE.
DEFAULT_CANONICAL_BASE = "https://prior-auth.example/fhir"

FHIR_DISCLAIMER = (
    "Synthetic demo artifact derived from a reviewed policy pack (CMS-0057 / "
    "Da Vinci DTR alignment). No live payer API was called. Human review is "
    "required before submission."
)


def _questionnaire_id(pack: PolicySet) -> str:
    for req in pack.documentation_requirements:
        if req.dtr_questionnaire_id:
            return req.dtr_questionnaire_id
    return f"{pack.policy_set_id}-dtr"


def _link_id(req: DocumentationRequirement) -> str:
    return req.dtr_questionnaire_item_link_id or req.requirement_id


def _item_type(req: DocumentationRequirement) -> str:
    if req.attachment_required or req.requirement_type == "attachment":
        return "attachment"
    return "boolean"


def _local_ext(name: str, key: str, value) -> dict:
    return {"url": f"{LOCAL_EXT_BASE}/{name}", key: value}


def _information_origin(source: str = "auto") -> dict:
    """DTR information-origin extension: how an answer was populated."""
    return {
        "url": DTR_INFORMATION_ORIGIN_EXT,
        "extension": [{"url": "source", "valueCode": source}],
    }


def questionnaire_from_pack(
    pack: PolicySet,
    *,
    canonical_base: str = DEFAULT_CANONICAL_BASE,
) -> dict:
    """Render a policy pack's documentation requirements as a FHIR Questionnaire."""
    qid = _questionnaire_id(pack)
    items: list[dict] = []
    for req in pack.documentation_requirements:
        extensions = [_local_ext("requirement-id", "valueString", req.requirement_id)]
        if req.requirement_type:
            extensions.append(
                _local_ext("requirement-type", "valueString", req.requirement_type)
            )
        if req.lookback_period:
            extensions.append(
                _local_ext("lookback-period", "valueString", req.lookback_period)
            )
        if req.clinician_attestation_required:
            extensions.append(
                _local_ext("clinician-attestation-required", "valueBoolean", True)
            )
        items.append({
            "linkId": _link_id(req),
            "text": req.description,
            "type": _item_type(req),
            "required": bool(req.required and not req.conditional),
            "extension": extensions,
        })

    questionnaire = {
        "resourceType": "Questionnaire",
        "id": qid,
        "meta": {"profile": [DTR_QUESTIONNAIRE_PROFILE]},
        "url": f"{canonical_base.rstrip('/')}/Questionnaire/{qid}",
        "version": pack.policy_version or "1",
        "title": f"{pack.payer} — {pack.policy_name} documentation requirements",
        "status": "active",
        "subjectType": ["Patient"],
        "publisher": pack.payer,
        "description": (
            f"Payer documentation requirements for {pack.policy_name} "
            f"(procedure codes {', '.join(pack.procedure_codes)}). {FHIR_DISCLAIMER}"
        ),
        "item": items,
    }
    date = pack.last_reviewed_at or pack.effective_date
    if date:
        questionnaire["date"] = date
    return questionnaire


def _library_from_pack(pack: PolicySet, canonical_base: str) -> dict:
    """Medical-necessity rules as a logic Library (human-readable, no CQL yet)."""
    lid = f"{pack.policy_set_id}-necessity-rules"
    lines = [
        f"{c.criterion_id} [{c.required_status}] {c.criterion_name}: "
        f"{c.rule_expression} — {c.human_readable_rationale}"
        for c in pack.medical_necessity_criteria
    ]
    citations = sorted({
        c.policy_citation for c in pack.medical_necessity_criteria if c.policy_citation
    })
    library = {
        "resourceType": "Library",
        "id": lid,
        "url": f"{canonical_base.rstrip('/')}/Library/{lid}",
        "version": pack.policy_version or "1",
        "title": f"{pack.policy_name} medical-necessity rules",
        "status": "active",
        "type": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/library-type",
                "code": "logic-library",
            }],
        },
        "publisher": pack.payer,
        "description": (
            "Human-readable medical-necessity rules from the policy pack. No "
            f"executable CQL is included (see PRD cross-cutting backlog). {FHIR_DISCLAIMER}"
        ),
        "content": [{
            "contentType": "text/plain",
            "data": base64.b64encode("\n".join(lines).encode("utf-8")).decode("ascii"),
        }],
    }
    if citations:
        library["relatedArtifact"] = [
            {"type": "citation", "display": c} for c in citations
        ]
    return library


def questionnaire_package_from_pack(
    pack: PolicySet,
    *,
    canonical_base: str = DEFAULT_CANONICAL_BASE,
) -> dict:
    """Parameters payload shaped like the DTR ``$questionnaire-package`` output.

    The real operation returns one ``return`` parameter per questionnaire, each
    a collection Bundle holding the Questionnaire and its Libraries.
    """
    return {
        "resourceType": "Parameters",
        "parameter": [{
            "name": "return",
            "resource": {
                "resourceType": "Bundle",
                "type": "collection",
                "entry": [
                    {"resource": questionnaire_from_pack(pack, canonical_base=canonical_base)},
                    {"resource": _library_from_pack(pack, canonical_base)},
                ],
            },
        }],
    }


def questionnaire_response_from_assessment(
    assessment: dict,
    pack: PolicySet,
    request_data: dict,
    request_id: str,
    *,
    canonical_base: str = DEFAULT_CANONICAL_BASE,
) -> dict:
    """Pre-populated QuestionnaireResponse from a review's DTR-lite evaluation.

    ``assessment`` is the stored ``StandardsAssessment`` dict from a completed
    review (``ReviewResponse.standards``). MET requirements become answered
    items carrying their evidence and a DTR information-origin extension
    (source ``auto`` — populated from the chart, not typed by a human); unmet
    requirements stay unanswered and expose the gap action, so the remaining
    human work is explicit in the artifact itself.
    """
    dtr = assessment.get("dtr") or {}
    evaluations = dtr.get("requirement_evaluations") or []
    by_requirement = {req.requirement_id: req for req in pack.documentation_requirements}
    qid = _questionnaire_id(pack)

    items: list[dict] = []
    for evaluation in evaluations:
        req = by_requirement.get(evaluation.get("requirement_id", ""))
        status = evaluation.get("status", "MISSING")
        extensions = [
            _local_ext("evaluation-status", "valueString", status),
            _local_ext("evaluation-confidence", "valueInteger",
                       int(evaluation.get("confidence", 0))),
        ]
        gap_action = evaluation.get("gap_action") or ""
        if gap_action:
            extensions.append(_local_ext("gap-action", "valueString", gap_action))

        item = {
            "linkId": _link_id(req) if req else evaluation.get("requirement_id", ""),
            "text": evaluation.get("description", ""),
            "extension": extensions,
        }
        if status == "MET":
            evidence = [str(e) for e in (evaluation.get("evidence") or []) if str(e)]
            answer_extensions = [_information_origin("auto")]
            answer_extensions += [
                _local_ext("evidence", "valueString", e) for e in evidence
            ]
            if req and _item_type(req) == "attachment":
                answer = {
                    "valueAttachment": {
                        "contentType": "text/plain",
                        "title": evidence[0] if evidence else evaluation.get("description", ""),
                    },
                    "extension": answer_extensions,
                }
            else:
                answer = {"valueBoolean": True, "extension": answer_extensions}
            item["answer"] = [answer]
        items.append(item)

    required_met = all(
        e.get("status") == "MET"
        for e in evaluations
        if e.get("required") and not e.get("conditional")
    )
    response = {
        "resourceType": "QuestionnaireResponse",
        "id": f"dtr-response-{request_id}",
        "meta": {"profile": [DTR_QUESTIONNAIRE_RESPONSE_PROFILE]},
        "questionnaire": f"{canonical_base.rstrip('/')}/Questionnaire/{qid}",
        "status": "completed" if (evaluations and required_met) else "in-progress",
        "authored": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "author": {
            "display": (
                "Provider Prior Auth Accelerator — deterministic standards layer "
                "(draft; human review required)"
            ),
        },
        "extension": [_local_ext("disclaimer", "valueString", FHIR_DISCLAIMER)],
        "item": items,
    }
    patient_name = str(request_data.get("patient_name") or "")
    if patient_name:
        response["subject"] = {"display": patient_name}
    return response


# --- PAS request Bundle (PRD Epic 2) -----------------------------------------

ICD10_SYSTEM = "http://hl7.org/fhir/sid/icd-10-cm"
CPT_SYSTEM = "http://www.ama-assn.org/go/cpt"
NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"
CLAIM_INFO_CATEGORY_SYSTEM = (
    "http://terminology.hl7.org/CodeSystem/claiminformationcategory"
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _urn(request_id: str, resource_key: str) -> str:
    """Deterministic urn:uuid fullUrl for a bundle entry (stable per review)."""
    return "urn:uuid:" + str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"pas/{request_id}/{resource_key}")
    )


def _supporting_info_category(code: str, text: str = "") -> dict:
    category = {
        "coding": [
            {"system": CLAIM_INFO_CATEGORY_SYSTEM, "code": "info"},
            {"system": f"{LOCAL_CS_BASE}/supporting-info-category", "code": code},
        ],
    }
    if text:
        category["text"] = text
    return category


def pas_bundle_from_review(
    assessment: dict,
    pack: PolicySet,
    request_data: dict,
    request_id: str,
    *,
    canonical_base: str = DEFAULT_CANONICAL_BASE,
) -> dict:
    """Assemble a PAS-shaped request Bundle from a completed review.

    The Bundle is the exportable, standards-conformant view of the packet —
    it is built for inspection/export and is never submitted anywhere. When
    the review is not PAS-ready, the Claim carries a ``missing-for-submission``
    extension per open gap so an incomplete export is explicit about it.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    urls = {key: _urn(request_id, key) for key in (
        "claim", "patient", "coverage", "practitioner", "insurer",
        "location", "service-request", "questionnaire-response",
    )}

    def _ref(key: str) -> dict:
        return {"reference": urls[key]}

    def _rid(key: str) -> str:
        return urls[key].removeprefix("urn:uuid:")

    # --- Referenced resources -------------------------------------------------
    patient = {
        "resourceType": "Patient",
        "id": _rid("patient"),
        "name": [{"text": str(request_data.get("patient_name") or "")}],
    }
    dob = str(request_data.get("patient_dob") or "")
    if _DATE_RE.match(dob):
        patient["birthDate"] = dob

    insurer = {
        "resourceType": "Organization",
        "id": _rid("insurer"),
        "name": str(request_data.get("payer_name") or pack.payer),
        "active": True,
    }

    practitioner = {
        "resourceType": "Practitioner",
        "id": _rid("practitioner"),
        "name": [{"text": str(request_data.get("ordering_provider_name") or "")}],
    }
    npi = str(
        request_data.get("ordering_provider_npi")
        or request_data.get("provider_npi")
        or ""
    )
    if npi:
        practitioner["identifier"] = [{"system": NPI_SYSTEM, "value": npi}]

    coverage = {
        "resourceType": "Coverage",
        "id": _rid("coverage"),
        "status": "active",
        "beneficiary": _ref("patient"),
        "payor": [_ref("insurer")],
    }
    insurance_id = str(request_data.get("insurance_id") or "")
    if insurance_id:
        coverage["subscriberId"] = insurance_id
    plan = str(request_data.get("payer_plan") or pack.plan)
    if plan:
        coverage["class"] = [{
            "type": {"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/coverage-class",
                "code": "plan",
            }]},
            "value": plan,
        }]

    procedure_codes = [str(c) for c in (request_data.get("procedure_codes") or [])]
    diagnosis_codes = [str(c) for c in (request_data.get("diagnosis_codes") or [])]
    service_request = {
        "resourceType": "ServiceRequest",
        "id": _rid("service-request"),
        "status": "active",
        "intent": "order",
        "subject": _ref("patient"),
        "requester": _ref("practitioner"),
        "code": {
            "coding": [{"system": CPT_SYSTEM, "code": c} for c in procedure_codes],
            "text": ", ".join(procedure_codes),
        },
    }

    facility_name = str(request_data.get("servicing_facility") or "")
    location = None
    if facility_name:
        location = {
            "resourceType": "Location",
            "id": _rid("location"),
            "name": facility_name,
        }

    attached = [str(a) for a in (request_data.get("attached_note_types") or []) if str(a)]
    doc_urls = [_urn(request_id, f"document-{i}") for i in range(len(attached))]
    document_references = [
        {
            "resourceType": "DocumentReference",
            "id": doc_urls[i].removeprefix("urn:uuid:"),
            "status": "current",
            "subject": _ref("patient"),
            "description": title,
            # Stub attachment — the demo carries document *names*, not bytes.
            "content": [{"attachment": {"contentType": "text/plain", "title": title}}],
        }
        for i, title in enumerate(attached)
    ]

    questionnaire_response = questionnaire_response_from_assessment(
        assessment, pack, request_data, request_id, canonical_base=canonical_base,
    )
    questionnaire_response["id"] = _rid("questionnaire-response")

    # --- Claim ----------------------------------------------------------------
    supporting_info: list[dict] = [{
        "sequence": 1,
        "category": _supporting_info_category(
            "dtr-questionnaire-response", "DTR questionnaire response"
        ),
        "valueReference": _ref("questionnaire-response"),
    }, {
        "sequence": 2,
        "category": _supporting_info_category("requested-service", "Requested service"),
        "valueReference": _ref("service-request"),
    }]
    sequence = 3
    evaluations = (assessment.get("dtr") or {}).get("requirement_evaluations") or []
    for evaluation in evaluations:
        entry = {
            "sequence": sequence,
            "category": _supporting_info_category(
                "documentation-requirement", evaluation.get("description", "")
            ),
            "valueString": evaluation.get("status", "MISSING"),
            "extension": [
                _local_ext("requirement-id", "valueString",
                           evaluation.get("requirement_id", "")),
                _local_ext("evaluation-confidence", "valueInteger",
                           int(evaluation.get("confidence", 0))),
            ],
        }
        gap_action = evaluation.get("gap_action") or ""
        if gap_action:
            entry["extension"].append(_local_ext("gap-action", "valueString", gap_action))
        supporting_info.append(entry)
        sequence += 1
    for i, title in enumerate(attached):
        supporting_info.append({
            "sequence": sequence,
            "category": _supporting_info_category("attachment", title),
            "valueReference": {"reference": doc_urls[i]},
        })
        sequence += 1

    claim = {
        "resourceType": "Claim",
        "id": _rid("claim"),
        "meta": {"profile": [PAS_CLAIM_PROFILE]},
        "status": "active",
        "type": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/claim-type",
            "code": "professional",
        }]},
        "use": "preauthorization",
        "patient": _ref("patient"),
        "created": now,
        "insurer": _ref("insurer"),
        "provider": _ref("practitioner"),
        "priority": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/processpriority",
            "code": "stat" if request_data.get("urgency") == "urgent" else "normal",
        }]},
        "insurance": [{"sequence": 1, "focal": True, "coverage": _ref("coverage")}],
        "diagnosis": [
            {
                "sequence": i + 1,
                "diagnosisCodeableConcept": {
                    "coding": [{"system": ICD10_SYSTEM, "code": code}],
                },
            }
            for i, code in enumerate(diagnosis_codes)
        ],
        "item": [
            {
                "sequence": i + 1,
                "productOrService": {
                    "coding": [{"system": CPT_SYSTEM, "code": code}],
                },
            }
            for i, code in enumerate(procedure_codes)
        ],
        "supportingInfo": supporting_info,
    }
    specialty = str(request_data.get("rendering_provider_specialty") or "")
    if specialty:
        claim["careTeam"] = [{
            "sequence": 1,
            "provider": _ref("practitioner"),
            "qualification": {"text": specialty},
        }]
    if location:
        claim["facility"] = _ref("location")

    # Explicit incompleteness annotation — an export of a not-ready packet must
    # say so in the artifact itself, not only in the UI.
    pas = assessment.get("pas") or {}
    if not pas.get("pas_ready"):
        missing = [str(m) for m in (pas.get("missing_for_submission") or [])] or [
            "PAS readiness could not be evaluated for this review."
        ]
        claim["extension"] = [
            _local_ext("missing-for-submission", "valueString", m) for m in missing
        ]

    entries = [
        {"fullUrl": urls["claim"], "resource": claim},
        {"fullUrl": urls["patient"], "resource": patient},
        {"fullUrl": urls["coverage"], "resource": coverage},
        {"fullUrl": urls["practitioner"], "resource": practitioner},
        {"fullUrl": urls["insurer"], "resource": insurer},
        {"fullUrl": urls["service-request"], "resource": service_request},
        {"fullUrl": urls["questionnaire-response"], "resource": questionnaire_response},
    ]
    if location:
        entries.append({"fullUrl": urls["location"], "resource": location})
    entries += [
        {"fullUrl": doc_urls[i], "resource": doc}
        for i, doc in enumerate(document_references)
    ]

    return {
        "resourceType": "Bundle",
        "id": f"pas-request-{request_id}",
        "meta": {
            "profile": [PAS_REQUEST_BUNDLE_PROFILE],
            "tag": [{
                "system": f"{LOCAL_CS_BASE}/artifact-tag",
                "code": "synthetic-demo",
                "display": FHIR_DISCLAIMER,
            }],
        },
        "type": "collection",
        "timestamp": now,
        "entry": entries,
    }
