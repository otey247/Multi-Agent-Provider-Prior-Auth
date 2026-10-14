"""CRD CDS Hooks endpoints (PRD Epic 3 — Da Vinci CRD).

Mounted at the application root (no ``/api`` prefix) because CDS Hooks clients
expect discovery at ``{baseUrl}/cds-services``. All cards are derived from the
policy-pack matcher; no live payer API is called.
"""

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.models.cds_hooks import CdsDiscoveryResponse, CdsResponse
from app.services.standards.crd_hooks import CDS_SERVICES, build_crd_response

router = APIRouter()


@router.get("/cds-services", response_model=CdsDiscoveryResponse)
async def discover_cds_services():
    """CDS Hooks discovery. Empty services list when the CRD service is disabled."""
    if not settings.ENABLE_CRD_HOOKS:
        return CdsDiscoveryResponse(services=[])
    return CdsDiscoveryResponse(services=list(CDS_SERVICES.values()))


@router.post("/cds-services/{service_id}", response_model=CdsResponse)
async def invoke_cds_service(service_id: str, request: Request):
    """Invoke a CRD service and return coverage-requirement cards."""
    if not settings.ENABLE_CRD_HOOKS:
        raise HTTPException(
            status_code=503,
            detail="CRD CDS Hooks service is disabled (ENABLE_CRD_HOOKS=false)",
        )
    if service_id not in CDS_SERVICES:
        raise HTTPException(status_code=404, detail=f"CDS service {service_id} not found")

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — malformed body is a client error, not a 500
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="CDS Hooks request must be a JSON object")

    return build_crd_response(payload, str(request.base_url))
