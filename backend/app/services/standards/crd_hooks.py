"""CRD CDS Hooks handler (PRD Epic 3 — Da Vinci Coverage Requirements Discovery).

Turns a CDS Hooks ``order-select`` / ``order-sign`` request into decision cards
by extracting the requested service + coverage from the hook context and
matching it against the policy-pack store — the same deterministic matcher the
review pipeline uses. No live payer API is called; when nothing matches, the
card says so and points at the runtime Medicare LCD/NCD fallback.

Extraction is intentionally forgiving: EHRs place the draft orders and coverage
in different spots (``context.draftOrders`` bundle, ``context.selections``,
``prefetch``), so we walk every FHIR resource we can find and pull codes from
``ServiceRequest``/``DeviceRequest``/``MedicationRequest`` and payer/plan from
``Coverage``. ``build_crd_response`` never raises — on any unexpected shape it
returns a safe informational card so the service cannot 500.
"""

from __future__ import annotations

import logging

from app.models.cds_hooks import (
    CdsCard,
    CdsLink,
    CdsResponse,
    CdsServiceDefinition,
    CdsSource,
)
from app.services.policy_store import match_policy_pack

logger = logging.getLogger("app.crd_hooks")

SOURCE_LABEL = "Provider Prior Auth Accelerator — CRD (demo)"

# Advertised services. Both are handled by the same hook-agnostic logic; the id
# `prior-auth-crd` (order-select) is the one an EHR posts to first.
CDS_SERVICES: dict[str, CdsServiceDefinition] = {
    "prior-auth-crd": CdsServiceDefinition(
        hook="order-select",
        id="prior-auth-crd",
        title="Prior authorization coverage requirements discovery",
        description=(
            "Checks a selected order against reviewed payer policy packs and, "
            "when prior authorization is required, returns the routing channel "
            "and a link to the DTR questionnaire package. Provider-side; no live "
            "payer API is called."
        ),
        prefetch={
            "coverage": "Coverage?patient={{context.patientId}}",
        },
    ),
    "prior-auth-crd-sign": CdsServiceDefinition(
        hook="order-sign",
        id="prior-auth-crd-sign",
        title="Prior authorization coverage requirements discovery (order-sign)",
        description=(
            "Same coverage-requirements discovery, evaluated as the order is "
            "signed. Provider-side; no live payer API is called."
        ),
        prefetch={
            "coverage": "Coverage?patient={{context.patientId}}",
        },
    ),
}

_ORDER_RESOURCE_TYPES = {"ServiceRequest", "DeviceRequest", "MedicationRequest"}


def _codes(codeable_concept) -> list[str]:
    """Codes from a FHIR CodeableConcept (or a list of them)."""
    if isinstance(codeable_concept, list):
        out: list[str] = []
        for cc in codeable_concept:
            out.extend(_codes(cc))
        return out
    if not isinstance(codeable_concept, dict):
        return []
    return [
        str(c["code"])
        for c in (codeable_concept.get("coding") or [])
        if isinstance(c, dict) and c.get("code")
    ]


def _coverage_payer(cov: dict) -> str:
    for payor in (cov.get("payor") or []):
        if isinstance(payor, dict) and payor.get("display"):
            return str(payor["display"])
    return ""


def _coverage_plan(cov: dict) -> str:
    for cls in (cov.get("class") or []):
        if not isinstance(cls, dict):
            continue
        if "plan" in _codes(cls.get("type")):
            value = cls.get("value") or cls.get("name")
            if value:
                return str(value)
    return ""


def _iter_resources(request: dict):
    """Yield every FHIR resource we can find in the hook context + prefetch."""
    context = request.get("context") or {}
    prefetch = request.get("prefetch") or {}

    def _from_bundle_or_resource(obj):
        if not isinstance(obj, dict):
            return
        if obj.get("resourceType") == "Bundle":
            for entry in (obj.get("entry") or []):
                if isinstance(entry, dict) and isinstance(entry.get("resource"), dict):
                    yield entry["resource"]
        elif obj.get("resourceType"):
            yield obj

    # context.draftOrders / context.selections may hold Bundles or resources
    for key in ("draftOrders", "draftMedicationRequests", "orders", "selections"):
        yield from _from_bundle_or_resource(context.get(key))

    # prefetch values are individual resources or Bundles
    if isinstance(prefetch, dict):
        for value in prefetch.values():
            yield from _from_bundle_or_resource(value)


def _extract(request: dict) -> dict:
    """Pull payer, plan, procedure codes, and diagnosis codes from a request."""
    procedure_codes: list[str] = []
    diagnosis_codes: list[str] = []
    payer = ""
    plan = ""

    for resource in _iter_resources(request):
        rtype = resource.get("resourceType")
        if rtype in _ORDER_RESOURCE_TYPES:
            procedure_codes += _codes(resource.get("code"))
            diagnosis_codes += _codes(resource.get("reasonCode"))
        elif rtype == "Coverage":
            payer = payer or _coverage_payer(resource)
            plan = plan or _coverage_plan(resource)
        elif rtype == "Condition":
            diagnosis_codes += _codes(resource.get("code"))

    # De-dupe while preserving order.
    def _dedupe(seq: list[str]) -> list[str]:
        seen: set[str] = set()
        return [x for x in seq if not (x in seen or seen.add(x))]

    return {
        "payer": payer,
        "plan": plan,
        "procedure_codes": _dedupe(procedure_codes),
        "diagnosis_codes": _dedupe(diagnosis_codes),
    }


def _no_match_card(extracted: dict) -> CdsCard:
    svc = ", ".join(extracted["procedure_codes"]) or "the selected order"
    return CdsCard(
        summary="No payer-specific prior-auth policy matched",
        indicator="info",
        detail=(
            f"No reviewed policy pack matched **{svc}**"
            + (f" for **{extracted['payer']}**" if extracted["payer"] else "")
            + ". Runtime Medicare LCD/NCD search applies during the full review; "
            "prior-auth requirements cannot be asserted from a policy pack here."
        ),
        source=CdsSource(label=SOURCE_LABEL),
    )


def _package_link(base_url: str, policy_set_id: str) -> CdsLink:
    base = base_url if base_url.endswith("/") else base_url + "/"
    return CdsLink(
        label="Open DTR questionnaire package",
        url=f"{base}api/policy-packs/{policy_set_id}/questionnaire-package",
        type="absolute",
    )


def build_crd_response(request: dict, base_url: str) -> CdsResponse:
    """Build CDS Hooks cards for a CRD request. Never raises (safe card on error)."""
    try:
        extracted = _extract(request)
        if not extracted["procedure_codes"]:
            return CdsResponse(cards=[CdsCard(
                summary="No orderable service found in the request context",
                indicator="info",
                detail=(
                    "The CDS Hooks request contained no ServiceRequest / "
                    "DeviceRequest / MedicationRequest with a procedure code to "
                    "evaluate for prior authorization."
                ),
                source=CdsSource(label=SOURCE_LABEL),
            )])

        match = match_policy_pack(
            payer_name=extracted["payer"] or None,
            payer_plan=extracted["plan"] or None,
            procedure_codes=extracted["procedure_codes"],
            diagnosis_codes=extracted["diagnosis_codes"],
        )

        if not match or not match.matched or not match.policy_set:
            return CdsResponse(cards=[_no_match_card(extracted)])

        pack = match.policy_set
        source = CdsSource(label=pack.payer or SOURCE_LABEL, url=pack.source_url or None)

        if match.pa_required is False:
            return CdsResponse(cards=[CdsCard(
                summary=f"No prior authorization required — {pack.policy_name}"[:140],
                indicator="info",
                detail=(
                    f"Matched policy pack **{pack.policy_name}** "
                    f"({pack.payer} {pack.plan}) indicates prior authorization is "
                    "not required for this order."
                ),
                source=source,
            )])

        channel = (
            f"Delegated UM vendor: {pack.delegated_vendor}"
            if pack.delegated_vendor
            else f"Payer portal ({pack.payer})"
        )
        n_reqs = len(pack.documentation_requirements)
        req_preview = "\n".join(
            f"- {r.description}" for r in pack.documentation_requirements[:5]
        )
        more = f"\n- …and {n_reqs - 5} more" if n_reqs > 5 else ""
        detail = (
            f"**Prior authorization required** per **{pack.policy_name}** "
            f"({pack.payer} {pack.plan}).\n\n"
            f"- **Routing:** {channel}\n"
            f"- **Documentation requirements:** {n_reqs}\n\n"
            f"{req_preview}{more}\n\n"
            "_Provider-side determination from a reviewed policy pack — no live "
            "payer API was called._"
        )
        return CdsResponse(cards=[CdsCard(
            summary=f"Prior authorization required — {pack.policy_name}"[:140],
            indicator="warning",
            detail=detail,
            source=source,
            links=[_package_link(base_url, pack.policy_set_id)],
        )])
    except Exception as exc:  # noqa: BLE001 — a CDS service must never 500
        logger.warning("CRD hook handling failed: %s", exc)
        return CdsResponse(cards=[CdsCard(
            summary="Coverage requirements could not be evaluated",
            indicator="info",
            detail=(
                "The CRD service could not evaluate this request. Proceed with "
                "the standard review; this card is non-blocking."
            ),
            source=CdsSource(label=SOURCE_LABEL),
        )])
