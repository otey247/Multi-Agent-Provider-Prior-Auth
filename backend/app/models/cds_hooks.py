"""CDS Hooks models for the CRD service (PRD Epic 3 — Da Vinci CRD).

These model the provider-side CDS Hooks *service* an EHR would call at order
time (``order-select`` / ``order-sign``) to discover coverage requirements.
The response cards are derived deterministically from the policy-pack matcher —
no live payer API is called.

Request models are intentionally permissive: the ``context`` and ``prefetch``
shapes vary by hook and by EHR, so we accept them as free-form dicts and pull
the fields we need in ``app.services.standards.crd_hooks``. Response models are
strict so the emitted cards conform to the CDS Hooks 1.0 spec.
"""

from pydantic import BaseModel


# --- Discovery (GET /cds-services) -------------------------------------------


class CdsServiceDefinition(BaseModel):
    """One advertised CDS service in the discovery document."""

    hook: str                       # e.g. "order-select" | "order-sign"
    title: str = ""
    description: str = ""
    id: str                         # POST /cds-services/{id}
    prefetch: dict = {}             # FHIR query templates the EHR may prefetch


class CdsDiscoveryResponse(BaseModel):
    services: list[CdsServiceDefinition] = []


# --- Service invocation (POST /cds-services/{id}) ----------------------------


class CdsHooksRequest(BaseModel):
    """A CDS Hooks service call. Permissive by design (hook-specific context)."""

    hook: str = ""
    hookInstance: str = ""
    context: dict = {}
    prefetch: dict = {}
    fhirServer: str = ""


class CdsLink(BaseModel):
    label: str
    url: str
    type: str = "absolute"          # "absolute" | "smart"


class CdsSource(BaseModel):
    label: str
    url: str | None = None


class CdsCard(BaseModel):
    summary: str                    # <= 140 chars per spec
    indicator: str = "info"         # "info" | "warning" | "critical"
    detail: str = ""                # markdown
    source: CdsSource
    links: list[CdsLink] = []


class CdsResponse(BaseModel):
    cards: list[CdsCard] = []
