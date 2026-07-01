"""Standalone smoke check for the policy-pack store (no pytest required).

Run from the backend/ directory:

    python scripts/check_policy_store.py

Verifies that the UHC lumbar-fusion pack loads and that the existing
orthopedics sample case matches it. Exits non-zero on failure.
"""

import sys
from pathlib import Path

# Make `app...` importable when run from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.policy_store import load_policy_packs, match_policy_pack  # noqa: E402


def main() -> int:
    packs = load_policy_packs()
    print(f"Loaded {len(packs)} policy pack(s):")
    for p in packs:
        print(
            f"  - {p.policy_set_id} | {p.payer} {p.plan} | "
            f"{len(p.documentation_requirements)} requirements, "
            f"{len(p.medical_necessity_criteria)} criteria"
        )
    if not packs:
        print("FAIL: no packs loaded")
        return 1

    # Orthopedics sample case (frontend/lib/sample-case.ts -> orthopedic-surgery)
    match = match_policy_pack(
        payer_name="UnitedHealthcare",
        payer_plan="Commercial HMO",
        procedure_codes=["22612", "22840"],
        diagnosis_codes=["M43.16", "M54.16", "M48.062"],
    )

    print("\nMatch for ortho sample case (UHC Commercial HMO, 22612/22840):")
    print(f"  matched      = {match.matched}")
    print(f"  policy_set   = {match.policy_set_id}")
    print(f"  pa_required  = {match.pa_required}")
    print(f"  confidence   = {match.confidence}")
    print(f"  reasons      = {match.reasons}")

    ok = (
        match.matched
        and match.policy_set_id == "uhc-commercial-lumbar-fusion-v1"
        and match.pa_required is True
        and match.confidence >= 0.9
    )

    # A non-matching request (different payer + procedure) must NOT match.
    no_match = match_policy_pack(
        payer_name="Aetna",
        payer_plan="Commercial PPO",
        procedure_codes=["99213"],
        diagnosis_codes=["J18.9"],
    )
    print(f"\nNon-matching request matched? {no_match.matched} (expected False)")

    ok = ok and not no_match.matched
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
