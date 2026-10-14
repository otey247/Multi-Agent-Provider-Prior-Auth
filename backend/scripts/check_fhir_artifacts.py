"""Standalone behavioral check for the FHIR artifact builders (PRD Epics 1–2).

Run from backend/:
    python scripts/check_fhir_artifacts.py

Builds the FHIR Questionnaire, $questionnaire-package Parameters, the
pre-populated QuestionnaireResponse, and the PAS request Bundle from the
flagship policy pack + the real orthopedics sample case (no agents, no
network). Exits non-zero on failure.
"""

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.policy_store import match_policy_pack  # noqa: E402
from app.services.standards import (  # noqa: E402
    build_standards_assessment,
    pas_bundle_from_review,
    questionnaire_from_pack,
    questionnaire_package_from_pack,
    questionnaire_response_from_assessment,
)

# Same orthopedics sample case as scripts/check_standards.py.
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

EXPECTED_LINK_IDS = {
    "duration", "conservative-care", "imaging", "instability",
    "function", "pt-records", "tobacco", "specialty",
}


def main() -> int:
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        ok = ok and cond

    match = match_policy_pack(
        payer_name=ORTHO_REQUEST["payer_name"],
        payer_plan=ORTHO_REQUEST["payer_plan"],
        procedure_codes=ORTHO_REQUEST["procedure_codes"],
        diagnosis_codes=ORTHO_REQUEST["diagnosis_codes"],
    )
    if not (match and match.matched and match.policy_set):
        print("FAIL: flagship policy pack did not match — cannot continue")
        return 1
    pack = match.policy_set

    # --- Questionnaire ---
    q = questionnaire_from_pack(pack)
    link_ids = [i["linkId"] for i in q["item"]]
    types = {i["linkId"]: i["type"] for i in q["item"]}
    required = {i["linkId"]: i["required"] for i in q["item"]}

    print("Questionnaire:")
    print(f"  id={q['id']}  url={q['url']}")
    print(f"  items={len(q['item'])}  linkIds={link_ids}")

    print("\nAssertions (Questionnaire):")
    check("resourceType Questionnaire", q["resourceType"] == "Questionnaire")
    check("id from pack dtr_questionnaire_id", q["id"] == "uhc-lumbar-fusion-dtr")
    check("canonical url ends with /Questionnaire/{id}",
          q["url"].endswith(f"/Questionnaire/{q['id']}"))
    check("DTR profile in meta.profile", any("davinci-dtr" in p for p in q["meta"]["profile"]))
    check("8 items", len(q["item"]) == 8)
    check("linkIds unique", len(set(link_ids)) == len(link_ids))
    check("linkIds match pack DTR item link ids", set(link_ids) == EXPECTED_LINK_IDS)
    check("imaging + pt-records are attachment items",
          types.get("imaging") == "attachment" and types.get("pt-records") == "attachment")
    check("duration is a boolean item", types.get("duration") == "boolean")
    check("conditional tobacco item not required", required.get("tobacco") is False)
    check("pt-records required", required.get("pt-records") is True)
    check("disclaimer present", "No live payer API" in q["description"])

    # --- $questionnaire-package Parameters ---
    pkg = questionnaire_package_from_pack(pack)
    bundle = pkg["parameter"][0]["resource"]
    entry_types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    library = bundle["entry"][1]["resource"]
    library_text = base64.b64decode(library["content"][0]["data"]).decode("utf-8")

    print("\nAssertions ($questionnaire-package):")
    check("resourceType Parameters", pkg["resourceType"] == "Parameters")
    check("parameter named 'return'", pkg["parameter"][0]["name"] == "return")
    check("collection Bundle", bundle["resourceType"] == "Bundle" and bundle["type"] == "collection")
    check("Bundle holds Questionnaire + Library", entry_types == ["Questionnaire", "Library"])
    check("Library is a logic-library",
          library["type"]["coding"][0]["code"] == "logic-library")
    check("Library text carries the 3 necessity criteria",
          all(cid in library_text for cid in
              ("mnc-conservative-therapy", "mnc-instability", "mnc-imaging-symptom-correlation")))

    # --- QuestionnaireResponse (flagship case: 6/8 MET -> in-progress) ---
    assessment = build_standards_assessment(match, ORTHO_REQUEST, {}, {}).model_dump()
    qr = questionnaire_response_from_assessment(assessment, pack, ORTHO_REQUEST, "check-run-1")
    answered = [i for i in qr["item"] if i.get("answer")]
    unanswered = [i for i in qr["item"] if not i.get("answer")]

    print("\nQuestionnaireResponse (flagship case):")
    print(f"  status={qr['status']}  answered={len(answered)}/{len(qr['item'])}")
    for i in unanswered:
        print(f"  unanswered: {i['linkId']}")

    def _ext(item_or_answer, suffix):
        return [e for e in item_or_answer.get("extension", []) if e["url"].endswith(suffix)]

    print("\nAssertions (QuestionnaireResponse):")
    check("resourceType QuestionnaireResponse", qr["resourceType"] == "QuestionnaireResponse")
    check("references the Questionnaire canonical", qr["questionnaire"] == q["url"])
    check("status in-progress (open gaps)", qr["status"] == "in-progress")
    check("8 items", len(qr["item"]) == 8)
    check("6 answered", len(answered) == 6)
    check("pt-records + tobacco unanswered",
          {i["linkId"] for i in unanswered} == {"pt-records", "tobacco"})
    check("subject is the patient", qr["subject"]["display"] == "Thomas Reed")
    check("every answered item carries DTR information-origin (auto)",
          all(
              any(
                  e["url"].endswith("information-origin")
                  and e["extension"][0]["valueCode"] == "auto"
                  for e in i["answer"][0].get("extension", [])
              )
              for i in answered
          ))
    check("every item carries an evaluation-status extension",
          all(_ext(i, "evaluation-status") for i in qr["item"]))
    check("unanswered items expose a gap action",
          all(_ext(i, "gap-action") for i in unanswered))
    check("imaging answered as attachment",
          "valueAttachment" in next(i for i in answered if i["linkId"] == "imaging")["answer"][0])
    check("duration answered as boolean true",
          next(i for i in answered if i["linkId"] == "duration")["answer"][0].get("valueBoolean") is True)

    # --- Gap-free variant -> completed ---
    complete_request = dict(ORTHO_REQUEST)
    complete_request["attached_note_types"] = ORTHO_REQUEST["attached_note_types"] + [
        "Physical therapy discharge summary",
    ]
    complete_request["clinical_notes"] = (
        ORTHO_REQUEST["clinical_notes"]
        + " Smoking cessation counseling completed; patient is a former smoker."
    )
    complete_assessment = build_standards_assessment(match, complete_request, {}, {}).model_dump()
    qr_done = questionnaire_response_from_assessment(
        complete_assessment, pack, complete_request, "check-run-2",
    )
    done_answered = [i for i in qr_done["item"] if i.get("answer")]

    print("\nAssertions (gap-free variant):")
    check("status completed", qr_done["status"] == "completed")
    check("all 8 items answered", len(done_answered) == 8)

    # --- PAS request Bundle (flagship case: not PAS-ready) ---
    bundle = pas_bundle_from_review(assessment, pack, ORTHO_REQUEST, "check-run-1")
    entries = bundle["entry"]
    resource_types = [e["resource"]["resourceType"] for e in entries]
    claim = entries[0]["resource"]
    full_urls = {e["fullUrl"] for e in entries}
    references = []

    def _collect_refs(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "reference" and isinstance(value, str):
                    references.append(value)
                else:
                    _collect_refs(value)
        elif isinstance(node, list):
            for value in node:
                _collect_refs(value)

    _collect_refs(bundle)

    def _si_by_code(code):
        return [
            si for si in claim["supportingInfo"]
            if any(c.get("code") == code for c in si["category"]["coding"])
        ]

    print("\nPAS request Bundle (flagship case):")
    print(f"  entries={len(entries)}  types={sorted(set(resource_types))}")
    print(f"  supportingInfo={len(claim['supportingInfo'])}")

    print("\nAssertions (PAS Bundle):")
    check("collection Bundle with PAS profile",
          bundle["type"] == "collection"
          and any("davinci-pas" in p for p in bundle["meta"]["profile"]))
    check("synthetic-demo tag on Bundle",
          any(t.get("code") == "synthetic-demo" for t in bundle["meta"].get("tag", [])))
    check("Claim is the first entry", resource_types[0] == "Claim")
    check("Claim.use preauthorization", claim["use"] == "preauthorization")
    check("8 documentation-requirement supportingInfo entries",
          len(_si_by_code("documentation-requirement")) == 8)
    check("QuestionnaireResponse referenced from supportingInfo",
          len(_si_by_code("dtr-questionnaire-response")) == 1)
    check("4 attachment supportingInfo entries", len(_si_by_code("attachment")) == 4)
    check("supportingInfo sequences are 1..N",
          [si["sequence"] for si in claim["supportingInfo"]]
          == list(range(1, len(claim["supportingInfo"]) + 1)))
    check("3 diagnoses (ICD-10)", len(claim["diagnosis"]) == 3)
    check("2 items (CPT)", len(claim["item"]) == 2)
    check("4 DocumentReference entries", resource_types.count("DocumentReference") == 4)
    check("Patient/Coverage/Practitioner/Organization/ServiceRequest/QR present",
          all(rt in resource_types for rt in (
              "Patient", "Coverage", "Practitioner", "Organization",
              "ServiceRequest", "QuestionnaireResponse")))
    check("facility Location present (servicing facility set)",
          "Location" in resource_types and "facility" in claim)
    check("all bundle-internal references resolve to fullUrls",
          all(r in full_urls for r in references if r.startswith("urn:uuid:")))
    check("not-ready Claim carries missing-for-submission extensions",
          any(e["url"].endswith("missing-for-submission")
              for e in claim.get("extension", [])))
    check("missing annotation mentions the PT discharge gap",
          any("discharge" in e.get("valueString", "").lower()
              for e in claim.get("extension", [])))
    check("deterministic rebuild (stable urn:uuid ids)",
          pas_bundle_from_review(assessment, pack, ORTHO_REQUEST, "check-run-1")["entry"][0]["fullUrl"]
          == entries[0]["fullUrl"])

    # --- PAS Bundle for the gap-free variant: ready, no annotation ---
    bundle_done = pas_bundle_from_review(
        complete_assessment, pack, complete_request, "check-run-2",
    )
    claim_done = bundle_done["entry"][0]["resource"]
    print("\nAssertions (PAS Bundle, gap-free variant):")
    check("ready Claim has no missing-for-submission extension",
          not any(e["url"].endswith("missing-for-submission")
                  for e in claim_done.get("extension", [])))
    check("5 DocumentReference entries (extra PT discharge attachment)",
          sum(1 for e in bundle_done["entry"]
              if e["resource"]["resourceType"] == "DocumentReference") == 5)

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
