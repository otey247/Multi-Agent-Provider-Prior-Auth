"""End-to-end check of the standards layer against a running backend.

Posts the orthopedics sample case to POST /api/review and inspects the
`standards` block in the response. Works against any deployment.

Usage (backend URL via arg or env; trailing /api optional):
    python scripts/e2e_standards.py https://<backend-host>
    BACKEND_URL=https://<backend-host> python scripts/e2e_standards.py

For an azd deployment:  BACKEND_URL="https://$(azd env get-value backendUrl)"
"""

import json
import os
import sys
import urllib.error
import urllib.request

ORTHO_PAYLOAD = {
    "patient_name": "Thomas Reed",
    "patient_dob": "1966-08-27",
    "provider_npi": "1669542008",
    "diagnosis_codes": ["M43.16", "M54.16", "M48.062"],
    "procedure_codes": ["22612", "22840"],
    "clinical_notes": (
        "59-year-old male with 14 months of refractory low back pain radiating to the "
        "left leg in an L5 distribution with numbness and neurogenic claudication. MRI lumbar "
        "spine 02/02/2026 shows grade 1 degenerative spondylolisthesis at L4-L5 with severe central "
        "canal stenosis and bilateral foraminal narrowing. Dynamic X-rays demonstrate instability with "
        "4 mm translation on flexion-extension views.\n\n"
        "Symptoms worsen with standing more than 10 minutes or walking more than one block and interfere "
        "with work as a warehouse supervisor. Oswestry Disability Index 46%. Conservative treatment includes "
        "12 weeks of physical therapy, home exercise program, NSAIDs, gabapentin, two epidural steroid injections, "
        "and activity modification with only transient improvement.\n\n"
        "Exam: positive straight leg raise on left, dorsiflexion 4+/5, reduced sensation over lateral calf. "
        "No bowel or bladder dysfunction. Plan is L4-L5 decompression with instrumented fusion due to instability "
        "and failed conservative management. Risks, benefits, and alternatives reviewed with patient and spouse."
    ),
    "insurance_id": "UHC-84739122",
    "ordering_provider_name": "Meghan Osei, MD",
    "ordering_provider_npi": "1669542008",
    "rendering_provider_specialty": "Orthopedic Spine Surgery",
    "servicing_facility": "Summit Ambulatory Surgery Center",
    "payer_name": "UnitedHealthcare",
    "payer_plan": "Commercial HMO",
    "urgency": "standard",
    "place_of_service": "Ambulatory Surgery Center",
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


def resolve_url() -> str:
    raw = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("BACKEND_URL", "")).strip()
    if not raw:
        print("ERROR: pass the backend URL as an argument or set BACKEND_URL.")
        print('  e.g. python scripts/e2e_standards.py https://<backend-host>')
        sys.exit(2)
    base = raw.rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    if base.endswith("/api"):
        base = base[: -len("/api")]
    return f"{base}/api/review"


def main() -> int:
    url = resolve_url()
    print(f"POST {url}  (multi-agent review can take 1-5 min)...\n")
    body = json.dumps(ORTHO_PAYLOAD).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=360) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode(errors='replace')[:500]}")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"Request failed: {e}")
        return 1

    print(f"recommendation : {data.get('recommendation')}  ({data.get('confidence_level')})")
    pv = ((data.get("agent_results") or {}).get("coverage") or {}).get("provider_verification") or {}
    print(f"provider status: {pv.get('status')}  ({pv.get('npi')})")

    standards = data.get("standards")
    if not standards:
        print("\nFAIL: response has no `standards` block. The deployed backend predates the "
              "standards layer — rebuild/redeploy the backend from this repo.")
        return 1

    crd = standards.get("crd") or {}
    dtr = standards.get("dtr") or {}
    pas = standards.get("pas") or {}
    print("\n-- standards --")
    print(f"  policy_pack_matched : {standards.get('policy_pack_matched')} ({standards.get('policy_set_id')})")
    print(f"  CRD pa_required     : {crd.get('pa_required')}  route={crd.get('routing_channel')}")
    print(f"  DTR met             : {dtr.get('requirements_met')}/{dtr.get('requirements_total')}")
    for ev in dtr.get("requirement_evaluations", []):
        print(f"    [{ev.get('status'):12}] {ev.get('requirement_id')}")
    print(f"  PAS ready           : {pas.get('pas_ready')}  channel={pas.get('submission_channel')}")

    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        ok = ok and cond

    print("\nAssertions:")
    check("policy pack matched", standards.get("policy_pack_matched") is True)
    check("CRD pa_required True", crd.get("pa_required") is True)
    check("DTR 6/8 requirements met", dtr.get("requirements_met") == 6)
    check("PAS not ready (PT discharge gap)", pas.get("pas_ready") is False)

    # Informational: requirement-aware agents (needs agent redeploy to activate).
    # Not asserted — LLM output varies; this just shows whether the hosted
    # Compliance/Coverage agents consumed the injected policy pack.
    ar = data.get("agent_results") or {}
    cov = ar.get("coverage") or {}
    comp = ar.get("compliance") or {}
    psid = standards.get("policy_set_id", "")
    cov_pack = [c for c in (cov.get("criteria_assessment") or [])
                if psid and psid in str(c.get("source", ""))]
    comp_pack = [ci for ci in (comp.get("checklist") or [])
                 if str(ci.get("detail", "")).lstrip().startswith("[req-")]
    print("\n-- requirement-aware agents (informational; needs agent redeploy) --")
    print(f"  coverage criteria citing pack '{psid}': {len(cov_pack)}")
    for c in cov_pack[:8]:
        print(f"    - {c.get('status')}: {str(c.get('criterion'))[:66]}")
    print(f"  compliance checklist items citing requirement ids: {len(comp_pack)}")

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
