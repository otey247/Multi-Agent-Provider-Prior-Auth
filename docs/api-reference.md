# API Reference

## `POST /api/review`

Submit a prior authorization request for multi-agent review.
Returns the complete result as a single JSON response (no streaming).
Prefer `POST /api/review/stream` for the frontend — this endpoint is
useful for programmatic/API integrations that don't need progress updates.

**Request body:**

```json
{
  "patient_name": "Maria Gonzalez",
  "patient_dob": "1971-11-02",
  "provider_npi": "1437223344",
  "diagnosis_codes": ["C78.7", "C18.7", "Z92.21"],
  "procedure_codes": ["J9303", "96413"],
  "clinical_notes": "Metastatic colorectal cancer progressing after FOLFOX plus bevacizumab...",
  "insurance_id": "BCBS-TX-4472019",
  "ordering_provider_name": "David Lin, MD",
  "ordering_provider_npi": "1437223344",
  "rendering_provider_specialty": "Medical Oncology",
  "servicing_facility": "River Bend Cancer Institute Infusion Center",
  "payer_name": "Blue Cross Blue Shield",
  "payer_plan": "Commercial PPO",
  "urgency": "urgent",
  "place_of_service": "Office",
  "attached_note_types": [
    "Oncology progress note",
    "Treatment history summary",
    "CT abdomen/pelvis report",
    "Pathology and biomarker report"
  ],
  "prior_treatment_history": [
    "Completed FOLFOX plus bevacizumab with subsequent progression"
  ]
}
```

**Important request fields:**

| Field | Required | Purpose |
|-------|----------|---------|
| `patient_name`, `patient_dob` | Yes | Patient identity for provider packet preparation |
| `provider_npi` | Yes | Billing/submitting provider credential check |
| `diagnosis_codes`, `procedure_codes` | Yes | Core ICD-10 / CPT / HCPCS context for the review |
| `clinical_notes` | Yes | Narrative that explains medical necessity and current clinical status |
| `insurance_id` | No | Member identifier when available |
| `ordering_provider_name`, `ordering_provider_npi` | No | Useful for order-driven workflows and FHIR `ServiceRequest` mappings |
| `rendering_provider_specialty` | No | Helps explain specialty-procedure fit |
| `servicing_facility`, `place_of_service` | No | Adds operational context for surgery, infusion, imaging, and home-based workflows |
| `payer_name`, `payer_plan` | No | Distinguishes payer/product-specific routing |
| `urgency` | No | Supports standard vs urgent workflow handling |
| `attached_note_types` | No | Identifies which documents are already attached to the packet |
| `prior_treatment_history` | No | Captures conservative treatment or prior therapy context from upstream systems |

**Response** (top-level synthesis + per-agent breakdown + audit trail):

```json
{
  "request_id": "uuid",
  "recommendation": "ready_to_submit",
  "confidence": 0.87,
  "confidence_level": "HIGH",
  "summary": "All three agents report clean findings...",
  "tool_results": [...],
  "clinical_rationale": "Gate 1 PASS: Provider NPI active. Gate 2 PASS: All ICD-10 codes valid...",
  "coverage_criteria_met": ["Criterion — evidence"],
  "coverage_criteria_not_met": [],
  "missing_documentation": [],
  "documentation_gaps": [
    {"what": "Prior imaging results", "critical": false, "request": "Please provide X-ray reports"}
  ],
  "policy_references": ["NCD 150.7 — Joint Replacement"],
  "disclaimer": "AI-assisted draft. Coverage policies reflect Medicare LCDs/NCDs only...",
  "agent_results": {
    "compliance": {
      "checklist": [...],
      "overall_status": "complete",
      "missing_items": []
    },
    "clinical": {
      "diagnosis_validation": [{"code": "M17.11", "valid": true, "billable": true, "hierarchy_note": ""}],
      "procedure_validation": [{"code": "27447", "valid": true, "source": "orchestrator_preflight"}],
      "clinical_extraction": {
        "chief_complaint": "...",
        "extraction_confidence": 82
      },
      "literature_support": [...],
      "clinical_trials": [...],
      "clinical_summary": "...",
      "tool_results": [...]
    },
    "coverage": {
      "provider_verification": {"npi": "...", "status": "active"},
      "criteria_assessment": [
        {"criterion": "...", "status": "MET", "confidence": 85, "evidence": [...]}
      ],
      "documentation_gaps": [...]
    }
  },
  "audit_trail": {
    "data_sources": ["CPT/HCPCS Format Validation (Local)", "NPI Registry MCP (NPPES)", "ICD-10 MCP (2026 Code Set)"],
    "review_started": "2026-02-13T10:30:00Z",
    "review_completed": "2026-02-13T10:30:45Z",
    "extraction_confidence": 82,
    "assessment_confidence": 78,
    "criteria_met_count": "4/5"
  }
}
```

---

## `POST /api/review/stream`

Submit a prior authorization request with **real-time SSE progress streaming**.
Same request body as `POST /api/review`. Returns `text/event-stream`.

The frontend uses `fetch` + `ReadableStream` (not `EventSource`, which only
supports GET) to consume this endpoint.

**SSE event types:**

| Event | When | Payload |
|-------|------|---------|
| `progress` | At each phase boundary (9 total) | `{phase, status, progress_pct, message, agents}` |
| `result` | Review complete | Full `ReviewResponse` JSON |
| `error` | Pipeline failure | `{detail: "error message"}` |
| `: keepalive` | Every 2s during long agent runs | SSE comment (ignored by client) |

**Progress event example:**

```json
{
  "phase": "phase_1",
  "status": "running",
  "progress_pct": 10,
  "message": "Running Compliance and Clinical agents in parallel",
  "agents": {
    "compliance": {"status": "running", "detail": "Checking documentation completeness"},
    "clinical": {"status": "running", "detail": "Validating codes and extracting clinical evidence"}
  }
}
```

**Phase IDs:** `preflight` → `phase_1` → `phase_2` → `phase_3` → `phase_4`

**Agent statuses:** `pending` → `running` → `done` | `error`

---

## `GET /health`

Health check endpoint. Returns `{"status": "ok"}`.

---

## `GET /api/review/{request_id}`

Retrieve a previously completed review by its request ID.

**Response:** Same `ReviewResponse` structure as `POST /api/review`.

Returns `404` if the request ID is not found in the review store.

---

## `GET /api/reviews`

List all completed reviews (most recent first).

**Response:**

```json
[
  {
    "request_id": "uuid",
    "patient_name": "John Smith",
    "recommendation": "ready_to_submit",
    "confidence_level": "HIGH",
    "reviewed_at": "2026-02-13T10:30:45Z",
    "decision_made": false
  }
]
```

---

## `POST /api/decision`

Submit a human reviewer decision (accept or override) for a completed review.
Generates an authorization number and notification letter.

**Request body (accept):**

```json
{
  "request_id": "uuid",
  "action": "accept",
  "reviewer_name": "Dr. Jane Doe"
}
```

**Request body (override):**

```json
{
  "request_id": "uuid",
  "action": "override",
  "override_recommendation": "ready_to_submit",
  "override_rationale": "Clinical evidence supports approval despite agent uncertainty...",
  "reviewer_name": "Dr. Jane Doe"
}
```

**Response:**

```json
{
  "request_id": "uuid",
  "authorization_number": "PA-20260213-00001",
  "final_recommendation": "ready_to_submit",
  "decided_by": "Dr. Jane Doe",
  "decided_at": "2026-02-13T11:05:00Z",
  "was_overridden": true,
  "override_rationale": "Clinical evidence supports approval despite agent uncertainty...",
  "original_recommendation": "needs_review",
  "letter": {
    "authorization_number": "PA-20260213-00001",
    "letter_type": "approval",
    "effective_date": "2026-02-13",
    "expiration_date": "2026-05-14",
    "patient_name": "John Smith",
    "provider_name": "Dr. ...",
    "body_text": "PRIOR AUTHORIZATION — APPROVED ...",
    "appeal_rights": null,
    "documentation_deadline": null,
    "pdf_base64": "JVBERi0xLjQg..."
  },
  "updated_audit_justification_pdf": "JVBERi0xLjQg..."
}
```

When `was_overridden` is `true`, `override_rationale` and
`original_recommendation` are included. The notification letter contains a
"Clinician Override Notice" section. The `updated_audit_justification_pdf`
contains a regenerated audit PDF with Section 9 ("Clinician Override Record").

**Error responses:**
- `404` — Review not found
- `409` — Decision already recorded for this review
- `422` — Invalid action or missing override fields

---

## Per-Agent Endpoints

These endpoints expose each agent individually for per-agent evaluation,
red-teaming, integration testing, and future microservices migration.
The orchestrator dispatches to the equivalent Foundry Hosted Agents over their
Responses-protocol endpoints when running the full pipeline.

All per-agent responses share a common envelope:

```json
{
  "agent": "<agent-name>",
  "started": "2026-02-13T10:30:00Z",
  "completed": "2026-02-13T10:30:12Z",
  "result": { ... }
}
```

### `POST /api/agents/clinical`

Run the **Clinical Reviewer Agent** in isolation. Returns diagnosis validation, clinical extraction, literature support, clinical trials, and clinical summary.

**Request body:** Same `PriorAuthRequest` as `POST /api/review`.

**Response `result`:** Same structure as `agent_results.clinical` in the full review response.

---

### `POST /api/agents/compliance`

Run the **Compliance Validation Agent** in isolation. Returns the compliance checklist, documentation status, and missing items.

**Request body:** Same `PriorAuthRequest` as `POST /api/review`.

**Response `result`:** Same structure as `agent_results.compliance` in the full review response.

---

### `POST /api/agents/coverage`

Run the **Coverage Assessment Agent** in isolation. Requires clinical findings from a prior Clinical Agent run (or test fixtures).

**Request body:**

```json
{
  "request": {
    "patient_name": "John Smith",
    "patient_dob": "1955-03-15",
    "provider_npi": "1234567890",
    "diagnosis_codes": ["M17.11"],
    "procedure_codes": ["27447"],
    "clinical_notes": "...",
    "insurance_id": "ABC123456"
  },
  "clinical_findings": {
    "diagnosis_validation": [{"code": "M17.11", "valid": true}],
    "clinical_extraction": {"chief_complaint": "bilateral knee OA"}
  }
}
```

**Response `result`:** Same structure as `agent_results.coverage` in the full review response.

---

### `POST /api/agents/synthesis`

Run the **Synthesis Decision Agent** in isolation. Requires all three upstream agent results (or test fixtures).

**Request body:**

```json
{
  "request": { ... },
  "compliance_result": { ... },
  "clinical_result": { ... },
  "coverage_result": { ... },
  "cpt_validation": null
}
```

**Response `result`:** The final synthesis output (recommendation, confidence, decision gates, rationale).

---

## Provider Integration Examples

### EHR / FHIR-driven review before submission

Use `POST /api/review` or `POST /api/review/stream` after assembling a packet from EHR data such as:
- `Patient` and `Coverage` for demographics and payer context
- `ServiceRequest` for the requested service
- `DocumentReference` and `DiagnosticReport` for attachments
- `Encounter`, `Condition`, and `Observation` for supporting clinical context

### Resubmit after missing documentation

1. Run an initial review.
2. Capture `missing_documentation` and `documentation_gaps` from the response.
3. Attach the missing note types or clinical addenda upstream.
4. Re-submit the updated packet to the same review endpoint.

### Human override with audit trail

1. Retrieve the completed review from `GET /api/review/{request_id}`.
2. Have a clinician or reviewer decide whether to submit as-is or revise.
3. Call `POST /api/decision` with `action: "submit"` or `action: "revise"`.
4. Persist the returned authorization/letter output and override rationale in the provider work queue or chart workflow.

---

## Key Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | REST API framework |
| `uvicorn` | ASGI server |
| `azure-ai-agentserver` | Microsoft Agent Framework (MAF) SDK |
| `httpx` | Async HTTP client (backend dispatch + MCP transport in agent containers) |
| `fpdf2` | PDF generation for notification letters |
| `pydantic` | Request/response validation + structured output models |
| `react` + `next` | Frontend SPA (Next.js static export) |
| `shadcn/ui` + `tailwindcss` | UI component library + utility-first CSS |
