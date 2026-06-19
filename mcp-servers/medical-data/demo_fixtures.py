"""Curated fallback data for the demo sample cases.

Used ONLY when a live NPPES / CMS Coverage call fails, times out, or returns
empty for a *known demo key* — so live technical demonstrations stay intact when
a public API is slow or down. Every fixture is tagged ``source:
"curated_fallback"`` so it is transparent that the value did not come from a live
lookup. Keys correspond to the four sample cases in
``frontend/lib/sample-case.ts`` (their NPIs, procedure codes, and the real
Medicare policy IDs those scenarios map to). Live lookups remain the default for
everything else.
"""
from __future__ import annotations

from typing import Any


def _icd(code: str, desc: str) -> dict[str, str]:
    return {"code": code, "description": desc}


# --- Provider (NPPES) fixtures: scenario-appropriate credentials -------------
NPI_FIXTURES: dict[str, dict[str, Any]] = {
    "1720180003": {  # Pulmonology — bronchoscopic biopsy
        "npi": "1720180003", "provider_type": "Individual", "name": "Sarah Patel, MD",
        "credential": "MD", "status": "Active",
        "specialty": "Pulmonary Disease", "taxonomy_code": "207RP1001X",
        "taxonomy_description": "Internal Medicine, Pulmonary Disease",
        "license": "MD-204417", "state": "CA",
        "address": "1200 North Valley Way, San Jose, CA, 95128",
        "phone": "408-555-0143",
        "taxonomies": [
            {"code": "207RP1001X", "desc": "Internal Medicine, Pulmonary Disease", "primary": True, "license": "MD-204417", "state": "CA"},
            {"code": "207R00000X", "desc": "Internal Medicine", "primary": False, "license": "MD-204417", "state": "CA"},
        ],
    },
    "1437223344": {  # Oncology — specialty infusion
        "npi": "1437223344", "provider_type": "Individual", "name": "David Lin, MD",
        "credential": "MD", "status": "Active",
        "specialty": "Medical Oncology", "taxonomy_code": "207RX0202X",
        "taxonomy_description": "Internal Medicine, Medical Oncology",
        "license": "MD-118209", "state": "TX",
        "address": "905 River Bend Dr, Austin, TX, 78701",
        "phone": "512-555-0199",
        "taxonomies": [
            {"code": "207RX0202X", "desc": "Internal Medicine, Medical Oncology", "primary": True, "license": "MD-118209", "state": "TX"},
            {"code": "207RH0003X", "desc": "Internal Medicine, Hematology & Oncology", "primary": False, "license": "MD-118209", "state": "TX"},
        ],
    },
    "1669542008": {  # Orthopedic spine surgery — lumbar fusion
        "npi": "1669542008", "provider_type": "Individual", "name": "Meghan Osei, MD",
        "credential": "MD", "status": "Active",
        "specialty": "Orthopaedic Surgery of the Spine", "taxonomy_code": "207XS0117X",
        "taxonomy_description": "Orthopaedic Surgery, Orthopaedic Surgery of the Spine",
        "license": "MD-330612", "state": "OH",
        "address": "44 Summit Medical Plaza, Columbus, OH, 43215",
        "phone": "614-555-0178",
        "taxonomies": [
            {"code": "207XS0117X", "desc": "Orthopaedic Surgery, Orthopaedic Surgery of the Spine", "primary": True, "license": "MD-330612", "state": "OH"},
            {"code": "207X00000X", "desc": "Orthopaedic Surgery", "primary": False, "license": "MD-330612", "state": "OH"},
        ],
    },
    "1912084401": {  # Pulmonary NP — home oxygen DME
        "npi": "1912084401", "provider_type": "Individual", "name": "Janice Monroe, NP",
        "credential": "NP", "status": "Active",
        "specialty": "Nurse Practitioner", "taxonomy_code": "363L00000X",
        "taxonomy_description": "Nurse Practitioner",
        "license": "RN-558820", "state": "WA",
        "address": "60 Harbor Pulmonary Way, Seattle, WA, 98101",
        "phone": "206-555-0164",
        "taxonomies": [
            {"code": "363L00000X", "desc": "Nurse Practitioner", "primary": True, "license": "RN-558820", "state": "WA"},
            {"code": "363LP2300X", "desc": "Nurse Practitioner, Primary Care", "primary": False, "license": "RN-558820", "state": "WA"},
        ],
    },
}


# --- Coverage document fixtures, keyed by Medicare policy display id ----------
# covered_icd10 deliberately includes each scenario's submitted diagnosis codes
# so the per-code coverage matrix renders meaningfully in fallback mode.
COVERAGE_DOCS: dict[str, dict[str, Any]] = {
    "110.17": {
        "document_id": "110.17", "type": "NCD",
        "title": "Anti-Cancer Chemotherapy for Colorectal Cancer",
        "covered_icd10": [_icd("C18.7", "Malignant neoplasm of sigmoid colon"),
                          _icd("C78.7", "Secondary malignant neoplasm of liver and intrahepatic bile duct")],
        "noncovered_icd10": [],
        "hcpcs": [_icd("J9303", "Injection, panitumumab, 10 mg"),
                  _icd("96413", "Chemotherapy administration, intravenous infusion, up to 1 hour")],
    },
    "L37848": {
        "document_id": "L37848", "type": "LCD", "title": "Lumbar Spinal Fusion",
        "covered_icd10": [_icd("M43.16", "Spondylolisthesis, lumbar region"),
                          _icd("M54.16", "Radiculopathy, lumbar region"),
                          _icd("M48.062", "Spinal stenosis, lumbar region with neurogenic claudication")],
        "noncovered_icd10": [],
        "hcpcs": [_icd("22612", "Arthrodesis, posterior or posterolateral technique, single interspace; lumbar"),
                  _icd("22840", "Posterior non-segmental instrumentation")],
    },
    "240.2": {
        "document_id": "240.2", "type": "NCD", "title": "Home Use of Oxygen",
        "covered_icd10": [_icd("J44.1", "Chronic obstructive pulmonary disease with (acute) exacerbation"),
                          _icd("J96.11", "Chronic respiratory failure with hypoxia"),
                          _icd("R09.02", "Hypoxemia")],
        "noncovered_icd10": [],
        "hcpcs": [_icd("E1390", "Oxygen concentrator, single delivery port"),
                  _icd("E0431", "Portable gaseous oxygen system, rental")],
    },
    "BRONCH-DX": {
        "document_id": "BRONCH-DX", "type": "Article",
        "title": "Billing and Coding: Bronchoscopy and Transbronchial Biopsy",
        "covered_icd10": [_icd("R91.1", "Solitary pulmonary nodule"),
                          _icd("J18.9", "Pneumonia, unspecified organism"),
                          _icd("R05.9", "Cough, unspecified")],
        "noncovered_icd10": [],
        "hcpcs": [_icd("31628", "Bronchoscopy with transbronchial lung biopsy, single lobe")],
    },
}

# Search fallback: token/code -> policy summaries to surface from a coverage search.
_SEARCH_RULES: list[dict[str, Any]] = [
    {"match": {"31628", "bronchoscopy", "biopsy", "nodule"},
     "policies": [{"policy_id": "BRONCH-DX", "title": "Billing and Coding: Bronchoscopy and Transbronchial Biopsy", "type": "Article"}]},
    {"match": {"j9303", "96413", "panitumumab", "chemotherapy", "colorectal"},
     "policies": [{"policy_id": "110.17", "title": "Anti-Cancer Chemotherapy for Colorectal Cancer", "type": "NCD"}]},
    {"match": {"22612", "22840", "fusion", "lumbar", "spinal", "spine"},
     "policies": [{"policy_id": "L37848", "title": "Lumbar Spinal Fusion", "type": "LCD"}]},
    {"match": {"e1390", "e0431", "oxygen", "concentrator", "hypoxemia", "copd"},
     "policies": [{"policy_id": "240.2", "title": "Home Use of Oxygen", "type": "NCD"}]},
]


def npi_fallback(npi: str) -> dict[str, Any] | None:
    """Curated provider record for a known demo NPI, or None."""
    fx = NPI_FIXTURES.get(str(npi or "").strip())
    if fx is None:
        return None
    return {"found": True, "source": "curated_fallback", **fx}


def search_fallback(keyword: str) -> list[dict[str, Any]]:
    """Curated policy summaries for a known demo keyword/code, or []."""
    tokens = {t for t in (keyword or "").lower().replace(",", " ").split() if t}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in _SEARCH_RULES:
        if tokens & rule["match"]:
            for p in rule["policies"]:
                if p["policy_id"] not in seen:
                    seen.add(p["policy_id"])
                    out.append({**p, "relevant": True, "source": "curated_fallback"})
    return out


def document_fallback(document_id: str) -> dict[str, Any] | None:
    """Curated coverage document for a known demo policy id, or None."""
    doc = COVERAGE_DOCS.get(str(document_id or "").strip())
    if doc is None:
        return None
    return {"source": "curated_fallback", **doc}
