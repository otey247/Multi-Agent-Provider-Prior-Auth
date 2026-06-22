"""PHI redaction for execution-trace payloads.

Tool requests/responses captured for the in-app Debug Console are mostly public
reference data (ICD descriptions, NPI registry, Medicare policy), but a tool
argument can echo patient context, so we scrub before the payload leaves the
container. Two layers: (1) the known PHI values from the request (patient name,
DOB, insurance id) passed in by the caller, and (2) generic patterns (dates,
SSN, long digit runs, phone, email). Best-effort and non-fatal.
"""
from __future__ import annotations

import re

_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "[DATE]"),            # ISO date / DOB
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"), "[DATE]"),       # US date / DOB
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b\d{9,}\b"), "[ID]"),                          # MRN / long ids
]


def redact(text: str, phi_values: list[str] | None = None) -> str:
    """Return ``text`` with known PHI values and generic PHI patterns masked."""
    if not text:
        return text
    out = str(text)
    for value in phi_values or []:
        v = str(value or "").strip()
        if len(v) >= 3:
            out = out.replace(v, "[REDACTED]")
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out
