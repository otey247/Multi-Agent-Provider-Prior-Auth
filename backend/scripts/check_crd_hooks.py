"""Standalone behavioral check for the CRD CDS Hooks service (PRD Epic 3).

Run from backend/:
    python scripts/check_crd_hooks.py

Exercises the discovery document and card generation against the flagship
policy pack (UHC Commercial lumbar fusion) plus an unmatched request — no
agents, no network. Exits non-zero on failure.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.standards.crd_hooks import (  # noqa: E402
    CDS_SERVICES,
    build_crd_response,
)

BASE_URL = "http://localhost:8000/"


def cds_request(hook: str, cpt: str, payer: str, plan: str, icd: str) -> dict:
    return {
        "hook": hook,
        "hookInstance": "hook-instance-1",
        "context": {
            "userId": "Practitioner/meghan-osei",
            "patientId": "Patient/thomas-reed",
            "draftOrders": {
                "resourceType": "Bundle",
                "type": "collection",
                "entry": [{
                    "resource": {
                        "resourceType": "ServiceRequest",
                        "status": "draft",
                        "intent": "order",
                        "code": {"coding": [{
                            "system": "http://www.ama-assn.org/go/cpt", "code": cpt,
                        }]},
                        "reasonCode": [{"coding": [{
                            "system": "http://hl7.org/fhir/sid/icd-10-cm", "code": icd,
                        }]}],
                    },
                }],
            },
        },
        "prefetch": {
            "coverage": {
                "resourceType": "Coverage",
                "status": "active",
                "payor": [{"display": payer}],
                "class": [{
                    "type": {"coding": [{"code": "plan"}]},
                    "value": plan,
                }],
            },
        },
    }


def main() -> int:
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        ok = ok and cond

    # --- Discovery ---
    print("Discovery:")
    hooks = {s.hook for s in CDS_SERVICES.values()}
    ids = set(CDS_SERVICES.keys())
    for s in CDS_SERVICES.values():
        print(f"  {s.id}  hook={s.hook}  prefetch={list(s.prefetch.keys())}")

    print("\nAssertions (discovery):")
    check("advertises an order-select service", "order-select" in hooks)
    check("advertises an order-sign service", "order-sign" in hooks)
    check("prior-auth-crd id present (US-3.2 path)", "prior-auth-crd" in ids)
    check("every service advertises a coverage prefetch",
          all("coverage" in s.prefetch for s in CDS_SERVICES.values()))

    # --- Matched case: CPT 22612 / UHC Commercial -> PA required ---
    resp = build_crd_response(
        cds_request("order-select", "22612", "UnitedHealthcare", "Commercial HMO", "M43.16"),
        BASE_URL,
    )
    print("\nMatched case (CPT 22612 / UnitedHealthcare Commercial HMO):")
    for c in resp.cards:
        print(f"  [{c.indicator}] {c.summary}")
        for link in c.links:
            print(f"    link: {link.label} -> {link.url}")

    print("\nAssertions (matched case):")
    check("exactly one card", len(resp.cards) == 1)
    card = resp.cards[0]
    check("indicator warning", card.indicator == "warning")
    check("summary says prior authorization required",
          "prior authorization required" in card.summary.lower())
    check("summary within 140 chars", len(card.summary) <= 140)
    check("routing channel in detail (UHC portal)",
          "portal" in card.detail.lower() and "unitedhealthcare" in card.detail.lower())
    check("has a questionnaire-package link",
          any("/questionnaire-package" in link.url for link in card.links))
    check("link points at the matched pack",
          any("uhc-commercial-lumbar-fusion-v1" in link.url for link in card.links))
    check("source carries the payer label", "UnitedHealthcare" in (card.source.label or ""))

    # --- Order-sign hook works the same ---
    resp_sign = build_crd_response(
        cds_request("order-sign", "22612", "UnitedHealthcare", "Commercial HMO", "M43.16"),
        BASE_URL,
    )
    check("order-sign hook also returns a warning card",
          len(resp_sign.cards) == 1 and resp_sign.cards[0].indicator == "warning")

    # --- Unmatched case: CPT 99213 / Aetna -> info card, never 500 ---
    resp_nm = build_crd_response(
        cds_request("order-select", "99213", "Aetna", "PPO", "J18.9"), BASE_URL,
    )
    print("\nUnmatched case (CPT 99213 / Aetna PPO):")
    for c in resp_nm.cards:
        print(f"  [{c.indicator}] {c.summary}")

    print("\nAssertions (unmatched case):")
    check("returns one info card", len(resp_nm.cards) == 1 and resp_nm.cards[0].indicator == "info")
    check("no questionnaire-package link on unmatched card",
          not resp_nm.cards[0].links)

    # --- Robustness: empty / malformed request never raises, no order code ---
    resp_empty = build_crd_response({}, BASE_URL)
    check("empty request -> one info card (no 500)",
          len(resp_empty.cards) == 1 and resp_empty.cards[0].indicator == "info")
    resp_junk = build_crd_response({"context": "not-a-dict", "prefetch": 5}, BASE_URL)
    check("malformed context -> safe info card (no 500)",
          len(resp_junk.cards) == 1 and resp_junk.cards[0].indicator == "info")

    # --- Extraction also works when codes live in context.selections ---
    sel_request = {
        "hook": "order-select",
        "context": {
            "selections": {
                "resourceType": "ServiceRequest",
                "code": {"coding": [{"code": "22612"}]},
            },
            "draftOrders": {
                "resourceType": "Bundle",
                "entry": [{"resource": {
                    "resourceType": "Coverage",
                    "payor": [{"display": "UnitedHealthcare"}],
                    "class": [{"type": {"coding": [{"code": "plan"}]}, "value": "Commercial HMO"}],
                }}],
            },
        },
    }
    resp_sel = build_crd_response(sel_request, BASE_URL)
    check("code from context.selections still matches the pack",
          len(resp_sel.cards) == 1 and resp_sel.cards[0].indicator == "warning")

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
