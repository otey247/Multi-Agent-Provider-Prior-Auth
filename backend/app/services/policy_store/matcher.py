"""Match a prior-auth request to the best available policy pack.

Matching is intentionally simple and explainable (no ML): procedure-code
overlap is the primary signal, payer/plan/diagnosis overlap refine confidence,
and every match carries human-readable ``reasons`` for the audit trail.

This is the CRD-lite entry point — given a request, decide *which* payer policy
applies and (from its CoverageRule) whether prior auth is required.
"""

from __future__ import annotations

from app.models.standards import PolicySet, PolicyPackMatch
from app.services.policy_store.loader import load_policy_packs


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _payer_overlap(request_payer: str, pack_payer: str) -> bool:
    rp, pp = _norm(request_payer), _norm(pack_payer)
    if not rp or not pp:
        return False
    return rp in pp or pp in rp


def _plan_overlap(request_plan: str, pack_plan: str) -> bool:
    rp, pp = _norm(request_plan), _norm(pack_plan)
    if not rp or not pp:
        return False
    # token overlap so "Commercial HMO" matches "Commercial" / "HMO"
    return bool(set(rp.split()) & set(pp.split()))


def _score_pack(
    pack: PolicySet,
    *,
    payer_name: str,
    payer_plan: str,
    procedure_codes: list[str],
    diagnosis_codes: list[str],
) -> tuple[float, list[str]]:
    """Return (confidence 0-1, reasons). Procedure overlap is mandatory."""
    reasons: list[str] = []

    proc_set = {c.strip().upper() for c in procedure_codes if c.strip()}
    pack_procs = {c.strip().upper() for c in pack.procedure_codes}
    proc_hits = sorted(proc_set & pack_procs)
    if not proc_hits:
        return 0.0, []  # no procedure overlap -> not a candidate

    score = 0.5
    reasons.append(f"Procedure code match: {', '.join(proc_hits)}")

    if _payer_overlap(payer_name, pack.payer):
        score += 0.25
        reasons.append(f"Payer match: {pack.payer}")

    if _plan_overlap(payer_plan, pack.plan):
        score += 0.1
        reasons.append(f"Plan match: {pack.plan}")

    dx_set = {c.strip().upper() for c in diagnosis_codes if c.strip()}
    pack_dx = {c.strip().upper() for c in pack.diagnosis_codes}
    dx_hits = sorted(dx_set & pack_dx)
    if dx_hits:
        score += 0.15
        reasons.append(f"Diagnosis code match: {', '.join(dx_hits)}")

    return min(score, 1.0), reasons


def match_policy_pack(
    *,
    payer_name: str | None,
    payer_plan: str | None,
    procedure_codes: list[str],
    diagnosis_codes: list[str],
    minimum_confidence: float = 0.5,
) -> PolicyPackMatch:
    """Find the best-matching policy pack for a request.

    Returns an unmatched ``PolicyPackMatch`` (matched=False) when no pack clears
    ``minimum_confidence`` — callers fall back to the existing runtime search.
    """
    best: PolicySet | None = None
    best_score = 0.0
    best_reasons: list[str] = []

    for pack in load_policy_packs():
        score, reasons = _score_pack(
            pack,
            payer_name=payer_name or "",
            payer_plan=payer_plan or "",
            procedure_codes=procedure_codes,
            diagnosis_codes=diagnosis_codes,
        )
        if score > best_score:
            best, best_score, best_reasons = pack, score, reasons

    if best is None or best_score < minimum_confidence:
        return PolicyPackMatch(matched=False, confidence=round(best_score, 2))

    pa_required = None
    if best.coverage_rules:
        pa_required = any(r.pa_required for r in best.coverage_rules)

    return PolicyPackMatch(
        matched=True,
        policy_set_id=best.policy_set_id,
        payer=best.payer,
        plan=best.plan,
        delegated_vendor=best.delegated_vendor,
        pa_required=pa_required,
        confidence=round(best_score, 2),
        reasons=best_reasons,
        policy_set=best,
    )
