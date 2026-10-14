# PRD — CMS-0057-F / Da Vinci Deep Alignment

**Product:** Provider Prior Authorization Multi-Agent Solution Accelerator
**Scope:** Evolve the existing CRD/DTR/PAS-lite [standards layer](./cms-0057-standards-layer.md) into a demonstrably standards-conformant CMS-0057-F / HL7 Da Vinci implementation, plus the provider-side lifecycle features the rule makes operationally urgent.
**Status:** Draft v1.2 — Epics 1–3 implemented; Epics 4–8 ready for implementation
**Audience:** This PRD is written so each task is directly implementable by Claude Code — every task names the concrete files to create or edit and each epic has verifiable acceptance criteria.

---

## 1. Background & problem statement

CMS-0057-F (Interoperability and Prior Authorization Final Rule, January 2024) requires impacted payers to:

- Decide **expedited** PAs within **72 hours** and **standard** PAs within **7 calendar days** (effective January 1, 2026).
- Provide a **specific reason for denial** (effective January 1, 2026).
- Publicly report **prior authorization metrics** annually (first reports March 31, 2026).
- Implement a **Prior Authorization API** (recommended IGs: Da Vinci **CRD**, **DTR**, **PAS**), plus **Patient Access**, **Provider Access**, and **Payer-to-Payer** FHIR APIs (compliance date January 1, 2027).

MIPS adds an **Electronic Prior Authorization** measure for clinicians (CY 2027 performance period), which requires providers to request PAs electronically via the payer's CRD/DTR/PAS stack.

This accelerator is **provider-side**. Today its standards layer is "lite": policy packs drive a deterministic CRD/DTR/PAS *view* (`backend/app/services/standards/evaluator.py`), but no FHIR artifacts are produced, no CDS Hooks or FHIR operations exist, there is no post-submission lifecycle, and no metrics. The gap between "lite" and "conformant-shaped" is exactly what this PRD closes.

### Non-negotiable boundaries (inherited from the existing product)

1. **Provider-side only** — never approves/denies coverage.
2. **No live production payer API calls** — all payer behavior is either derived from reviewed policy packs or served by a **synthetic payer sandbox** shipped in this repo (Epic 4).
3. **Human review before submission** — nothing is auto-submitted to a real payer.
4. Everything is **feature-flagged and additive** — with flags off, the pipeline behaves exactly as today.

---

## 2. Goals & success metrics

| Goal | Metric |
|---|---|
| Produce standards-conformant Da Vinci artifacts from existing data | FHIR R4 `Questionnaire`, `QuestionnaireResponse`, and PAS `Bundle` generated for the flagship sample case; JSON validates against structural invariants in repo check scripts |
| Demonstrate the full CRD → DTR → PAS round trip end-to-end | Demo flow: CDS Hooks card → questionnaire package → pre-populated response → PAS bundle → sandbox `ClaimResponse`, with zero real payer calls |
| Support the 2026 operational requirements | Decision-clock timers (72h/7d), denial-reason capture, and an aging queue exist and are covered by check scripts |
| Preserve existing behavior | `scripts/check_standards.py` and `scripts/check_policy_store.py` still pass unchanged; with new flags off, API responses are byte-identical |

**Out of scope for this PRD:** production EHR integration, real payer connectivity, full CQL execution engine (stubbed/optional), X12 278 certification, Payer-to-Payer API (payer-side).

---

## 3. Personas

| Persona | Needs |
|---|---|
| **PA coordinator** (utilization review) | Know what the payer wants, close gaps, export a submission-ready package |
| **Integration engineer** (provider IT / EHR team) | Standards-shaped payloads and endpoints they can wire into an EHR or clearinghouse |
| **Revenue-cycle lead** | Turnaround visibility, denial-reason analytics, appeal readiness |
| **Compliance officer** | Evidence the org is ready for electronic PA (MIPS ePA measure, CMS-0057 timelines) |

---

## 4. Epics

Epics are ordered so each builds on the previous. Epic 1 converts the existing DTR-lite evaluation into real FHIR artifacts; Epic 2 does the same for PAS; Epic 3 exposes CRD as a real CDS Hooks service; Epic 4 adds the synthetic payer that makes the round trip live; Epics 5–7 add the CMS-0057 lifecycle/reporting features; Epic 8 makes intake FHIR-native.

---

### Epic 1 — DTR FHIR artifacts (Questionnaire + QuestionnaireResponse) ✅ *implemented*

> **Da Vinci IG:** DTR (Documentation Templates and Rules). **CMS-0057 tie-in:** the Prior Authorization API's documentation phase.

**Goal:** Every policy pack can be rendered as a FHIR R4 `Questionnaire` (and a DTR `$questionnaire-package`-shaped `Parameters` payload), and every review that matches a pack can produce a pre-populated `QuestionnaireResponse` whose answers carry chart-evidence provenance. This is the core DTR deliverable: payer questions answered from the chart automatically where possible, flagged for humans where not.

**User stories**

- **US-1.1** — As an *integration engineer*, I can `GET /api/policy-packs/{id}/questionnaire` and receive a FHIR R4 `Questionnaire` whose items correspond 1:1 to the pack's `documentation_requirements`, so I can preview exactly what a payer's DTR questionnaire would ask.
- **US-1.2** — As an *integration engineer*, I can `GET /api/policy-packs/{id}/questionnaire-package` and receive a `Parameters` resource shaped like the DTR `$questionnaire-package` operation output (Bundle containing the `Questionnaire` + a `Library` carrying the pack's medical-necessity rules), so my client code exercises the same shape a real payer endpoint would return.
- **US-1.3** — As a *PA coordinator*, after a review completes I can `GET /api/review/{request_id}/dtr/questionnaire-response` and receive a `QuestionnaireResponse` pre-populated from the chart: MET requirements are answered (with evidence and an origin extension), unmet requirements are unanswered and carry the gap action, so the remaining human work is explicit.
- **US-1.4** — As a *compliance officer*, the generated artifacts carry the standards disclaimer and a repo-scoped canonical base URL so nobody mistakes synthetic demo artifacts for a live payer feed.

**Tasks**

| # | Task | Files |
|---|---|---|
| 1.1 | Create the FHIR builder module: `questionnaire_from_pack()`, `questionnaire_package_from_pack()`, `questionnaire_response_from_assessment()` — pure functions, dict-based (no new dependencies), R4-shaped, DTR profile URLs in `meta.profile` | `backend/app/services/standards/fhir.py` *(new)* |
| 1.2 | Config: `ENABLE_FHIR_ARTIFACTS` (default `true`), `FHIR_CANONICAL_BASE` (default repo-scoped placeholder) | `backend/app/config.py` |
| 1.3 | Endpoints: `GET /policy-packs/{id}/questionnaire`, `GET /policy-packs/{id}/questionnaire-package`, `GET /review/{request_id}/dtr/questionnaire-response` (404 when pack/review/match absent; 503 when flag off) | `backend/app/routers/standards.py` |
| 1.4 | Export new builders from the standards package | `backend/app/services/standards/__init__.py` |
| 1.5 | Verification script asserting structural invariants (resourceType, canonical linkage, unique linkIds, answered/unanswered split for the flagship case) | `backend/scripts/check_fhir_artifacts.py` *(new)* |
| 1.6 | Docs: endpoints + flags + artifact description | `docs/cms-0057-standards-layer.md`, `docs/api-reference.md` |

**Acceptance criteria**

- `python scripts/check_fhir_artifacts.py` passes: flagship pack yields a `Questionnaire` with 8 items and unique `linkId`s matching the pack's `dtr_questionnaire_item_link_id`s; the flagship sample case yields a `QuestionnaireResponse` with 6 answered / 2 unanswered items, `status = "in-progress"`, and `questionnaire` referencing the Questionnaire's canonical URL.
- `scripts/check_standards.py` and `scripts/check_policy_store.py` still pass.
- With `ENABLE_FHIR_ARTIFACTS=false`, the new endpoints return 503 and nothing else changes.

---

### Epic 2 — PAS request Bundle builder + FHIR export UI ✅ *implemented*

> **Da Vinci IG:** PAS (Prior Authorization Support). **CMS-0057 tie-in:** the Prior Authorization API's submission phase.

**Goal:** Upgrade `PasPreview` from a readiness summary to an exportable, PAS-shaped FHIR `Bundle` — `Claim` (with `supportingInfo` sequencing and the `QuestionnaireResponse` attached), `Patient`, `Coverage`, `Practitioner`/`PractitionerRole`, `Organization`, `ServiceRequest`, and `DocumentReference` stubs for attachments. Surface "Export FHIR" actions in the Standards Alignment panel.

**User stories**

- **US-2.1** — As an *integration engineer*, I can `GET /api/review/{request_id}/pas/bundle` and receive a PAS-shaped `Bundle` (`Claim.use = "preauthorization"`) assembled from the review, so I can see exactly what would be submitted via `Claim/$submit`.
- **US-2.2** — As a *PA coordinator*, the bundle's `Claim.supportingInfo` enumerates each documentation requirement with its status, and attachments I named in `attached_note_types` appear as `DocumentReference` entries, so the payload mirrors my packet.
- **US-2.3** — As a *PA coordinator*, the Standards Alignment panel has **Export FHIR** buttons (Questionnaire, QuestionnaireResponse, PAS Bundle) that download the JSON for the current review.
- **US-2.4** — As a *compliance officer*, a not-PAS-ready review still exports a bundle but with an explicit `missing-for-submission` annotation so exports can't silently masquerade as complete.

**Tasks**

| # | Task | Files |
|---|---|---|
| 2.1 | PAS bundle builder: `pas_bundle_from_review(assessment, pack, request_data, request_id)` building Claim + referenced resources as contained/bundle entries; reuse patient/provider/payer fields from `request_data` (see `PriorAuthRequest` in `backend/app/models/schemas.py`) | `backend/app/services/standards/fhir.py` |
| 2.2 | Endpoint `GET /review/{request_id}/pas/bundle` (same flag + 404 semantics as Epic 1) | `backend/app/routers/standards.py` |
| 2.3 | Frontend API helpers for the three FHIR endpoints | `frontend/lib/api.ts` (or equivalent fetch layer) |
| 2.4 | "Export FHIR" download buttons in the panel (needs `request_id` prop — thread it from the dashboard) | `frontend/components/standards-panel.tsx`, `frontend/components/review-dashboard.tsx` |
| 2.5 | Extend the verification script with PAS-bundle invariants (bundle type, Claim.use, supportingInfo count = requirement count, DocumentReference count = attached_note_types count) | `backend/scripts/check_fhir_artifacts.py` |
| 2.6 | Docs: endpoint + UI description | `docs/cms-0057-standards-layer.md`, `docs/api-reference.md` |

**Acceptance criteria**

- Flagship case exports a Bundle whose `Claim` carries 8 `supportingInfo` entries and 4 `DocumentReference`s; `frontend: npx tsc --noEmit` is clean.
- Bundle export works for both PAS-ready and not-ready reviews, with the annotation present in the latter.

---

### Epic 3 — CRD as a CDS Hooks service ✅ *implemented*

> **Da Vinci IG:** CRD (Coverage Requirements Discovery). **CMS-0057 tie-in:** the Prior Authorization API's discovery phase.
>
> *Implementation note:* the questionnaire-package link is emitted as an `absolute` link (a direct FHIR endpoint URL) rather than a `smart` app-launch link, since the target is a data endpoint, not a SMART launch. Two services are advertised (`prior-auth-crd` / `prior-auth-crd-sign`); the handler is hook-agnostic.

**Goal:** Expose the policy-pack matcher as a real CDS Hooks service — the same interface an EHR would call at order time — returning cards ("PA required — 8 documentation requirements — launch DTR") derived from `match_policy_pack()`.

**User stories**

- **US-3.1** — As an *integration engineer*, `GET /cds-services` returns a discovery document advertising an `order-select` and an `order-sign` service, so a CDS Hooks sandbox (e.g. the public CDS Hooks sandbox) can list this service.
- **US-3.2** — As an *integration engineer*, `POST /cds-services/prior-auth-crd` with a CDS Hooks request (draft `ServiceRequest` + `Coverage` in `context`/`prefetch`) returns a card stating whether PA is required, the routing channel, and a link to the questionnaire package from Epic 1.
- **US-3.3** — As a *PA coordinator*, when no pack matches, the card says so and points at the runtime Medicare LCD/NCD fallback rather than pretending certainty.

**Tasks**

| # | Task | Files |
|---|---|---|
| 3.1 | CDS Hooks request/response models (hook, context, prefetch, cards with `summary`/`indicator`/`source`/`links`) | `backend/app/models/cds_hooks.py` *(new)* |
| 3.2 | Hook handler: extract payer/plan/CPT/ICD from the hook context (FHIR `ServiceRequest.code.coding`, `Coverage.payor.display`), call `match_policy_pack()`, build cards; `smart` link type pointing at the Epic 1 questionnaire-package URL | `backend/app/services/standards/crd_hooks.py` *(new)* |
| 3.3 | Router: `GET /cds-services`, `POST /cds-services/{service_id}`; mounted **without** the `/api` prefix (CDS Hooks clients expect root-level discovery) with flag `ENABLE_CRD_HOOKS` (default `true`) | `backend/app/routers/cds_hooks.py` *(new)*, `backend/app/main.py`, `backend/app/config.py` |
| 3.4 | Verification script: discovery shape, matched-case card, unmatched-case card | `backend/scripts/check_crd_hooks.py` *(new)* |
| 3.5 | Docs: CDS Hooks section with sample request/response | `docs/cms-0057-standards-layer.md`, `docs/api-reference.md` |

**Acceptance criteria**

- A hook request for CPT 22612 / UHC Commercial returns one card with `indicator = "warning"`, "Prior authorization required", the routing channel, and a link whose URL contains `/questionnaire-package`.
- An unmatched request returns an informational card, never a 500.

---

### Epic 4 — Synthetic payer sandbox (`payer-sim`)

> **Da Vinci IGs:** CRD + DTR + PAS payer side. **CMS-0057 tie-in:** demonstrates the full Prior Authorization API round trip without touching a real payer.

**Goal:** A small standalone FastAPI container (pattern: `mcp-servers/medical-data`) implementing the *payer-side* endpoints — CRD CDS Hooks, DTR `$questionnaire-package`, PAS `Claim/$submit` and `Claim/$inquire` — backed by the same policy packs. A `SUBMISSION_MODE=sandbox` flag lets the backend actually POST the Epic 2 bundle to it, upgrading the demo from "package preview" to "submitted → pended/approved" while keeping the no-real-payer boundary.

**User stories**

- **US-4.1** — As an *integration engineer*, I can run `docker compose up` and get a `payer-sim` service exposing `POST /fhir/Claim/$submit` that returns a PAS-shaped `ClaimResponse` (approved when the bundle's annotation says PAS-ready, pended otherwise), so the round trip is demonstrable locally.
- **US-4.2** — As a *PA coordinator*, with sandbox mode on, clicking **Submit to sandbox payer** on a review stores the returned `ClaimResponse` disposition against the review and shows it in the UI.
- **US-4.3** — As an *integration engineer*, `POST /fhir/Claim/$inquire` returns current status for a previously submitted PA, and pended submissions auto-progress (configurable delay) so status polling has something real to show.
- **US-4.4** — As a *compliance officer*, every sandbox response is labeled synthetic in a `ClaimResponse.disposition` suffix and the UI badge, so demo output can't be mistaken for a payer decision.

**Tasks**

| # | Task | Files |
|---|---|---|
| 4.1 | Scaffold the service (FastAPI + Dockerfile + README), mount policy packs read-only | `payer-sim/app/main.py`, `payer-sim/Dockerfile`, `payer-sim/README.md` *(new)* |
| 4.2 | Implement `$submit` / `$inquire` with an in-memory PA store and deterministic adjudication from the bundle annotation + pack rules | `payer-sim/app/adjudicator.py` *(new)* |
| 4.3 | Serve DTR `$questionnaire-package` and CRD hooks by importing the same builders (copy or shared package) | `payer-sim/app/routers/*.py` *(new)* |
| 4.4 | Backend sandbox client + `SUBMISSION_MODE` flag (`off` \| `sandbox`, default `off`); `POST /api/review/{request_id}/pas/submit` gated on it | `backend/app/services/standards/pas_client.py` *(new)*, `backend/app/routers/standards.py`, `backend/app/config.py` |
| 4.5 | Compose wiring + env examples | `docker-compose.yml`, `backend/.env.example` |
| 4.6 | UI: Submit-to-sandbox button + status badge on the Standards panel | `frontend/components/standards-panel.tsx` |
| 4.7 | E2E check script driving submit → inquire → status change | `backend/scripts/e2e_payer_sim.py` *(new)* |

**Acceptance criteria**

- `docker compose up` brings up payer-sim; submitting the flagship (not-ready) case returns a **pended** `ClaimResponse` listing the two gaps; a gap-free variant returns **approved**.
- With `SUBMISSION_MODE=off` (default) the submit endpoint returns 409 and no UI button renders.

---

### Epic 5 — PA lifecycle: status tracking, urgency, and decision-clock timers

> **CMS-0057 tie-in:** 72-hour expedited / 7-calendar-day standard decision timeframes (effective Jan 1, 2026).

**Goal:** Give reviews a post-submission lifecycle (`draft → submitted → pended → approved | denied | partially-approved`) with an urgency classification and countdown timers against the CMS decision clocks, surfaced in an aging queue.

**User stories**

- **US-5.1** — As a *PA coordinator*, each review has a lifecycle status I can advance (manually, or automatically in sandbox mode), so the tool tracks the PA after packet prep instead of stopping at readiness.
- **US-5.2** — As a *PA coordinator*, intake classifies a request **expedited** vs **standard** (rule-based: urgency field + red-flag keywords) with a stated reason, and the review shows the applicable decision deadline (submission time + 72h or + 7 days).
- **US-5.3** — As a *revenue-cycle lead*, `GET /api/reviews/queue` returns submitted-but-undecided PAs sorted by time-to-deadline, with `overdue` flagged, so follow-up is prioritized where the payer is out of time.
- **US-5.4** — As a *compliance officer*, every status transition is timestamped and appended to the review's audit trail.

**Tasks**

| # | Task | Files |
|---|---|---|
| 5.1 | Lifecycle models: `PaLifecycle` (status, urgency, submitted_at, decision_due_at, decided_at, transitions[]) added to the stored review record | `backend/app/models/schemas.py`, `backend/app/agents/orchestrator.py` (store helpers) |
| 5.2 | Urgency classifier (deterministic keyword/field rules; no LLM) + deadline computation (72h/7d) | `backend/app/services/lifecycle.py` *(new)* |
| 5.3 | Endpoints: `POST /api/review/{id}/lifecycle` (transition), `GET /api/reviews/queue` | `backend/app/routers/lifecycle.py` *(new)*, `backend/app/main.py` |
| 5.4 | Sandbox integration: Epic 4 submit/inquire drive transitions automatically | `backend/app/services/standards/pas_client.py` |
| 5.5 | Frontend: status/urgency badges on the review, `deadline in Xh` chip, aging-queue view | `frontend/components/*` |
| 5.6 | Check script: classifier cases, deadline math (incl. calendar-day vs hour semantics), queue ordering | `backend/scripts/check_lifecycle.py` *(new)* |

**Acceptance criteria**

- A red-flag clinical note (e.g. "progressive motor deficit") classifies expedited with a 72-hour deadline; the flagship case classifies standard with a 7-calendar-day deadline.
- Queue sorts by time remaining and flags overdue items; transitions append to the audit trail.

---

### Epic 6 — Denial reasons, appeal package, and CDex attachments loop

> **CMS-0057 tie-in:** specific denial reasons (Jan 1, 2026). **Da Vinci IG:** CDex (attachments / additional documentation).

**Goal:** Model payer denials with specific, coded reasons mapped back to the policy-pack requirement that failed; generate an appeal/resubmission package; handle the "payer pends and requests more documentation" loop CDex-style.

**User stories**

- **US-6.1** — As a *PA coordinator*, when a PA is denied I record (or sandbox-receive) coded denial reasons (CARC-style code + narrative), each linked to the pack requirement it corresponds to, so the fix is obvious.
- **US-6.2** — As a *PA coordinator*, one click generates an **appeal readiness package**: the original assessment, the denial reasons, the gap evidence added since, and a cover letter — reusing the existing letter/PDF generators.
- **US-6.3** — As a *PA coordinator*, a pended PA with an additional-documentation request shows exactly which attachments are wanted, matches them against `attached_note_types`, and prepares a `$submit-attachment`-shaped payload.
- **US-6.4** — As a *revenue-cycle lead*, denial reasons aggregate into the Epic 7 metrics so systemic documentation problems surface.

**Tasks**

| # | Task | Files |
|---|---|---|
| 6.1 | Denial models (`DenialReason`: code, narrative, linked_requirement_id) + storage on the lifecycle record | `backend/app/models/schemas.py` |
| 6.2 | Reason→requirement mapper (match denial codes/text to pack requirement ids) | `backend/app/services/denial_mapping.py` *(new)* |
| 6.3 | Appeal package generator (Markdown + PDF via existing `fpdf2` service) + endpoint `POST /api/review/{id}/appeal-package` | `backend/app/services/notification.py` or `appeal.py` *(new)*, `backend/app/routers/lifecycle.py` |
| 6.4 | CDex-shaped attachment request/response models + `$submit-attachment` payload builder; sandbox emits documentation requests for pended PAs | `backend/app/services/standards/fhir.py`, `payer-sim/app/adjudicator.py` |
| 6.5 | Frontend: denial panel, appeal-package download, attachment-request checklist | `frontend/components/*` |
| 6.6 | Check script covering mapping + appeal generation + attachment matching | `backend/scripts/check_denials.py` *(new)* |

**Acceptance criteria**

- A sandbox denial for the flagship case (with PT discharge gap unresolved) yields a denial reason linked to `req-pt-discharge-summary`; the appeal package names that requirement and includes the disclaimer.
- Attaching the missing document and resubmitting in sandbox mode flips the PA to approved.

---

### Epic 7 — Provider-side PA metrics dashboard

> **CMS-0057 tie-in:** payer public reporting (Mar 31, 2026) mirrored provider-side; MIPS **Electronic Prior Authorization** measure (CY 2027).

**Goal:** Aggregate review + lifecycle records into provider-side metrics: readiness rate, gap frequency by requirement, turnaround by payer, denial reasons, expedited share, and electronic-vs-portal submission mix.

**User stories**

- **US-7.1** — As a *revenue-cycle lead*, `GET /api/metrics/prior-auth` returns aggregate counts/rates (readiness rate, median prep time, approvals/denials, average payer turnaround, top gap reasons) filterable by payer and date range.
- **US-7.2** — As a *revenue-cycle lead*, a **Metrics** page renders those aggregates as cards + simple charts.
- **US-7.3** — As a *compliance officer*, an export (CSV/JSON) documents electronic-PA usage suitable as MIPS ePA measure supporting evidence.

**Tasks**

| # | Task | Files |
|---|---|---|
| 7.1 | Metrics aggregator over the in-memory store (design so a SQL backend can replace it per `docs/production-migration.md`) | `backend/app/services/metrics.py` *(new)* |
| 7.2 | Endpoints: `GET /api/metrics/prior-auth`, `GET /api/metrics/prior-auth/export` | `backend/app/routers/metrics.py` *(new)*, `backend/app/main.py` |
| 7.3 | Frontend metrics page (cards + charts consistent with existing design system) | `frontend/app/metrics/*`, `frontend/components/*` |
| 7.4 | Check script with synthetic review fixtures asserting aggregate math | `backend/scripts/check_metrics.py` *(new)* |

**Acceptance criteria**

- With three synthetic reviews (1 approved, 1 denied, 1 pended), the endpoint reports correct rates, turnaround, and top-gap ordering; export round-trips.

---

### Epic 8 — FHIR-native intake (US Core bundle ingestion)

> **Standards:** US Core / HRex. **Why:** honest DTR pre-population requires resolving `EvidenceMapping` entries against real FHIR resources instead of keyword matching.

**Goal:** Accept a FHIR R4 `Bundle` (US Core `Patient`, `Coverage`, `Condition`, `Procedure`, `Observation`, `DiagnosticReport`, `DocumentReference`) as an alternative intake path, map it to `PriorAuthRequest`, and let the DTR evaluator prefer resource-based evidence (via each pack's `evidence_mappings`) over keyword matching when a bundle is present.

**User stories**

- **US-8.1** — As an *integration engineer*, `POST /api/review/fhir` with a US Core bundle runs the same pipeline as `POST /api/review`, so EHR-side integration needs no bespoke JSON mapping.
- **US-8.2** — As a *PA coordinator*, when intake is FHIR-native, requirement evaluations cite the actual resource (e.g. `DiagnosticReport/mri-l4l5`) instead of a keyword match, raising evidence confidence.
- **US-8.3** — As an *integration engineer*, a sample US Core bundle for the flagship case ships in the repo so I can test without an EHR.

**Tasks**

| # | Task | Files |
|---|---|---|
| 8.1 | Bundle→`PriorAuthRequest` mapper with per-field provenance (`fhir_sources` map) | `backend/app/services/fhir_intake.py` *(new)* |
| 8.2 | Endpoint `POST /api/review/fhir` delegating to the existing review flow | `backend/app/routers/review.py` |
| 8.3 | Evaluator upgrade: when FHIR sources exist, resolve `EvidenceMapping.fhir_resource_type`/`fhir_path` against the bundle before falling back to keywords | `backend/app/services/standards/evaluator.py` |
| 8.4 | Sample bundle fixture for the flagship case | `backend/policy_packs/../fixtures/uhc-lumbar-fusion-uscore-bundle.json` *(new location: `backend/fixtures/`)* |
| 8.5 | Check script comparing keyword-path vs bundle-path evaluations | `backend/scripts/check_fhir_intake.py` *(new)* |

**Acceptance criteria**

- The sample bundle produces the same 6/8 MET outcome as the text sample, but MET evaluations cite resource ids; malformed bundles return 422 with actionable errors.

---

## 5. Cross-cutting backlog (post-epic candidates)

- **CQL execution** for `MedicalNecessityCriterion.rule_expression` (currently human-readable-authoritative), e.g. via a CQL-to-ELM service or `cql-execution`.
- **X12 278 stub generation** alongside the PAS bundle for the portal/clearinghouse routing channel.
- **SMART Backend Services auth** (client-credentials JWT) on payer-sim, optionally UDAP.
- **Additional policy packs**: specialty drug/infusion (site-of-care + line-of-therapy) and DME (face-to-face timing) to exercise the README scenarios.
- **FHIR Subscriptions (R4 backport)** on payer-sim for push-based status instead of `$inquire` polling.
- **Provider Access API client** (PDex prior-auth profiles) to pre-check for existing PAs at intake.

## 6. Sequencing & dependencies

```
Epic 1 (DTR artifacts) ──▶ Epic 2 (PAS bundle + UI) ──▶ Epic 4 (payer-sim submit)
        │                                                    │
        └────────▶ Epic 3 (CRD hooks) ──────────────────────┘
Epic 4 ──▶ Epic 5 (lifecycle) ──▶ Epic 6 (denials/appeals) ──▶ Epic 7 (metrics)
Epic 8 (FHIR intake) — independent after Epic 1; improves Epics 1–2 evidence quality
```

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| FHIR shape drift vs the actual Da Vinci IGs | Dict builders isolate shape in one module (`fhir.py`); check scripts pin invariants; profile URLs recorded in `meta.profile` for future validator runs |
| Scope creep toward payer-side adjudication | payer-sim adjudication is deterministic-from-pack, labeled synthetic, and lives in a separate container |
| Demo instability from added moving parts | Everything flag-gated; defaults preserve today's behavior; existing check scripts run unchanged in CI/verification |
| In-memory store limits lifecycle/metrics realism | Service layers written against store helper functions so the PostgreSQL migration path (`docs/production-migration.md`) drops in |

## 8. Glossary

| Term | Meaning |
|---|---|
| **CMS-0057-F** | CMS Interoperability and Prior Authorization Final Rule (Jan 2024) |
| **CRD** | Coverage Requirements Discovery — "is PA required?" via CDS Hooks |
| **DTR** | Documentation Templates and Rules — payer `Questionnaire` + rules, pre-populated from the chart |
| **PAS** | Prior Authorization Support — FHIR `Claim` bundle submission (`$submit`) + inquiry (`$inquire`) |
| **CDex** | Clinical Data Exchange — attachments / additional-documentation exchange |
| **PDex** | Payer Data Exchange — payer-held data (incl. prior auths) via Patient/Provider Access APIs |
| **MIPS ePA measure** | Electronic Prior Authorization measure — providers request PA electronically via the payer's CRD/DTR/PAS stack |
