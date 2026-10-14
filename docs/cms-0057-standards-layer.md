# CMS-0057 / Da Vinci Standards Layer

**What's new:** the accelerator now speaks **CMS-0057 / Da Vinci** (CRD / DTR / PAS). On top of the existing four-agent readiness pipeline, a **standards layer** answers the three questions a provider actually asks — *is a prior auth required and where does it route, exactly what does this payer want, and is my package ready to send* — using reusable, payer-specific **policy packs**.

> **Boundaries (unchanged):** This is provider-side only — it never approves/denies coverage. **No live payer API is called**; the CRD/DTR/PAS behavior is derived locally from reviewed policy packs, and pack content shipped in this repo is **synthetic demo data**. Human review is still required before submission. See [Boundaries & non-goals](#boundaries--non-goals).

---

## Why this was added

The prior version answered the provider's third question (submission readiness) well, but only partially answered the first two — it assumed PA was required and matched against **Medicare LCDs/NCDs only**, using a **generic 10-item** documentation checklist. For a commercial/Medicare-Advantage case that gap showed up directly (the orthopedics sample flagged *"pull the UnitedHealthcare lumbar-fusion policy"*).

| Provider question | Da Vinci phase | Before | Now |
|---|---|---|---|
| Is a PA required, and where does it route? | **CRD** | Assumed | Determined from the matched pack (PA-required + routing channel) |
| Exactly what does *this* payer want? | **DTR** | Generic 10-item checklist + Medicare policy | Payer/plan/procedure-specific requirement checklist, each item mapped to chart evidence |
| Is the package ready to send? | **PAS** | Gate-based readiness | Same, plus a package-preview with PAS-ready / channel / missing-for-submission |

The immediate value (per the CMS-0057 write-up that motivated this) is **grounding the model in the FHIR-aligned Da Vinci pattern** so future real CRD/DTR/PAS integrations drop in cleanly — without calling live payer APIs in the POC.

---

## Feature summary

- **Policy packs** — reusable, human-reviewed payer + plan + procedure + diagnosis requirement sets (JSON), loaded from disk. One pack drives the whole CRD/DTR/PAS experience.
- **CRD-lite** — deterministic "is PA required + routing channel (payer portal vs delegated UM vendor)" from the matched pack.
- **DTR-lite** — each payer documentation requirement evaluated against the chart/packet as **MET / INSUFFICIENT / MISSING**, with evidence and a specific gap action.
- **PAS-lite** — a prepared **package preview** (never submitted): PAS-ready / portal-ready flags, submission channel, and a missing-for-submission checklist.
- **Requirement-aware agents** — the hosted **Coverage** (Policy Matching) agent now treats a matched pack as the authoritative plan policy and maps evidence to each pack criterion; the **Compliance** agent can append payer-specific documentation requirements to its checklist.
- **Standards Alignment UI panel** — a collapsible CRD → DTR → PAS card in the assessment view.
- **Diagnostics + API** — an optional `standards` block on the review response, plus `GET /api/policy-packs` to list what's loaded.
- **FHIR artifacts (DTR)** — any pack renders as a FHIR R4 `Questionnaire` (and a DTR `$questionnaire-package`-shaped `Parameters` payload), and any completed review with a matched pack produces a pre-populated `QuestionnaireResponse` with per-answer evidence provenance. See [FHIR artifacts](#fhir-artifacts-da-vinci-dtr) and the [CMS-0057 / Da Vinci PRD](./prd-cms0057-davinci.md) (Epic 1).
- **Feature-flagged & additive** — everything degrades gracefully; when no pack matches or the layer is disabled, the pipeline behaves exactly as before.

---

## How it fits the pipeline

The standards layer wraps the existing four-agent flow — it does not replace it.

```
Intake ─▶ Pre-flight (CPT validation)
        └▶ CRD-lite: match_policy_pack()  ── injects requirement set ──┐
                                                                        ▼
        Phase 1  Documentation Completeness + Clinical Evidence   (Compliance gets policy_requirements)
        Phase 2  Policy Matching (Coverage)                       (Coverage gets payer_policy_pack)
        ├▶ demo-NPI verification fix (scoped)
        └▶ build_standards_assessment()  → CRD + DTR + PAS
        Phase 3  Submission Readiness (Synthesis)
        Phase 4  Audit trail + report
        ▶ ReviewResponse.standards  (+ "standards" trace phase in the Debug Console)
```

Two cooperating mechanisms:

1. **Deterministic standards service** (`backend/app/services/standards/`) — computes CRD/DTR/PAS from the matched pack + existing agent outputs using explainable field/keyword matching. This is the reliable, demo-stable source (same approach `coverage_enrich` uses to remove LLM variance).
2. **Requirement-aware agents** — the orchestrator also injects the pack into the Compliance and Coverage agent prompts so the agents *themselves* reason over the payer requirements (see [Requirement-aware agents](#requirement-aware-agents)).

---

## Policy packs

A **policy pack** is the central artifact. It lives under [`backend/policy_packs/<pack-id>/policy_set.json`](../backend/policy_packs/) and deserializes into `models.standards.PolicySet`.

> **Why under `backend/`:** the backend image builds from the `./backend` context (`az acr build … ./backend`), so a repo-root folder would not ship in the container. Packs are bundled at `backend/policy_packs/` (`COPY policy_packs/ ./policy_packs/`), resolved at `/app/policy_packs` in the container. JSON (not YAML) is used so loading needs no extra dependency.

### Structure

| Object | Purpose | FHIR / Da Vinci alignment |
|---|---|---|
| `PolicySet` | The pack: payer, plan, LOB, delegated vendor, procedure/diagnosis scope, version, source | — |
| `CoverageRule` | Whether PA is required + trigger conditions (CRD) | CRD / CDS Hooks |
| `DocumentationRequirement` | A required data element / document / attestation / attachment (DTR) | `Questionnaire` / `QuestionnaireResponse` |
| `MedicalNecessityCriterion` | A discrete medical-necessity criterion | DTR criterion / CQL rule (human-readable in lieu of CQL) |
| `EvidenceMapping` | Where/how to find evidence in the chart | `Condition`, `Procedure`, `Observation`, `DocumentReference`, … |

Field names intentionally mirror FHIR so a future real DTR/CRD feed can populate the same objects.

### Matching

`match_policy_pack(payer_name, payer_plan, procedure_codes, diagnosis_codes)` scores packs — **procedure-code overlap is mandatory**; payer, plan, and diagnosis overlap refine confidence — and returns an explainable `PolicyPackMatch` with `reasons`. Below `minimum_confidence` (default 0.5) it returns unmatched and the pipeline falls back to the existing Medicare runtime search.

### Authoring a new pack

1. Create `backend/policy_packs/<payer>-<plan>-<procedure>/policy_set.json` (copy the flagship pack as a template).
2. Fill in scope (`procedure_codes`, `diagnosis_codes`, `payer`, `plan`), a `CoverageRule`, `documentation_requirements`, `medical_necessity_criteria`, and `evidence_mappings`.
3. Verify locally: `cd backend && python scripts/check_policy_store.py`.
4. Redeploy (rebuilds the backend image) and confirm with `GET /api/policy-packs`.

The shipped example is **`uhc-commercial-lumbar-fusion-v1`** (UnitedHealthcare Commercial HMO, CPT 22612/22840): 8 documentation requirements + 3 medical-necessity criteria.

---

## The `standards` assessment

When enabled and a pack matches, `ReviewResponse.standards` (`models.standards.StandardsAssessment`) carries:

- **`crd`** (`CrdDetermination`) — `pa_required`, `routing_channel` (e.g. *"Payer portal (UnitedHealthcare)"* or *"Delegated UM vendor: …"*), `reasons`.
- **`dtr`** (`DtrAssessment`) — `requirements_met` / `requirements_total`, and a `requirement_evaluations[]` of `RequirementEvaluation` (status, confidence, evidence, `gap_action`).
- **`pas`** (`PasPreview`) — `pas_ready`, `portal_ready`, `submission_channel`, `missing_for_submission[]`, and a `package_summary`.

Evaluation is deterministic: each requirement is scored against the request fields (clinical notes, `prior_treatment_history`, `attached_note_types`) and the clinical extraction. Attachment-required items need the document named in `attached_note_types` (a narrative mention alone is `INSUFFICIENT`). `pas_ready` is true only when every **required, non-conditional** requirement is MET.

### Scoped demo-provider fix

`apply_demo_provider_verification()` marks the curated sample-case NPI (`DEMO_VERIFIED_NPIS = {1669542008}`) as **VERIFIED** so a fictional NPI doesn't block Gate 1 before the requirements story is reached. It is **skipped during coverage fallback**, so a real hosted-agent outage still surfaces honestly as unverified.

---

## Requirement-aware agents

The orchestrator injects a compact requirement set into the hosted agents (null when no pack, so default behavior is preserved):

- **Coverage / Policy Matching agent** — receives `payer_policy_pack`. Its [SKILL.md](../agents/coverage/skills/coverage-assessment/SKILL.md) Step 3b treats the pack as the **authoritative plan policy**, adds a `coverage_policies` entry for it, and maps clinical evidence to each pack criterion in `criteria_assessment` (`source` = pack id). ✅ **Verified live** — the flagship case produced 11 pack-sourced criteria (all 8 requirements + 3 criteria).
- **Compliance / Documentation Completeness agent** — receives `policy_requirements`. Its [SKILL.md](../agents/compliance/skills/compliance-review/SKILL.md) appends each payer requirement to the checklist beyond the standard 10 and adds unmet required ones to `missing_items`.

> Agent `SKILL.md` is inlined into the system prompt **at container startup** (`agents/*/main.py` `_load_skill()`), so changing an agent prompt requires an **agent image rebuild** (`azd up`) — a backend-only redeploy will not update agent behavior.

---

## FHIR artifacts (Da Vinci DTR)

The standards layer can render its policy-pack data and DTR-lite evaluations as real FHIR R4 resources ([PRD Epic 1](./prd-cms0057-davinci.md)). All builders live in [`backend/app/services/standards/fhir.py`](../backend/app/services/standards/fhir.py) as pure, dependency-free dict constructors, so the FHIR shape is isolated in one module.

| Artifact | Source | Endpoint |
|---|---|---|
| `Questionnaire` | Pack `documentation_requirements` (one item per requirement; `linkId` from `dtr_questionnaire_item_link_id`; attachment items where `attachment_required`) | `GET /api/policy-packs/{id}/questionnaire` |
| `Parameters` (DTR `$questionnaire-package` shape) | The `Questionnaire` plus a `Library` carrying `medical_necessity_criteria` as human-readable logic (no CQL yet) | `GET /api/policy-packs/{id}/questionnaire-package` |
| `QuestionnaireResponse` | A completed review's DTR-lite `requirement_evaluations` — MET items answered with evidence + the DTR `information-origin` extension (`source = auto`); unmet items unanswered with the gap action attached | `GET /api/review/{request_id}/dtr/questionnaire-response` |
| PAS request `Bundle` | The full packet as a Da Vinci PAS-shaped Bundle — `Claim` (`use = preauthorization`) with per-requirement `supportingInfo`, plus `Patient`, `Coverage`, `Practitioner`, payer `Organization`, `ServiceRequest`, the `QuestionnaireResponse`, and stub `DocumentReference`s for attachments. Export only — never submitted. Not-ready reviews carry a `missing-for-submission` extension per open gap | `GET /api/review/{request_id}/pas/bundle` |

Conventions:

- Da Vinci DTR profile URLs are recorded in `meta.profile`; repo-scoped extension URLs carry demo-only annotations (evaluation status, confidence, gap action, disclaimer).
- `QuestionnaireResponse.status` is `completed` only when every required, non-conditional requirement is MET — otherwise `in-progress`, so the artifact itself shows the remaining human work.
- Every artifact embeds the synthetic-demo disclaimer; the canonical base URL is a placeholder (`FHIR_CANONICAL_BASE`) so generated artifacts cannot be mistaken for a live payer feed.

Verified offline by `cd backend && python scripts/check_fhir_artifacts.py` (flagship pack → 8-item Questionnaire; flagship sample case → 6 answered / 2 unanswered, `in-progress`; gap-free variant → `completed`; PAS Bundle → Claim-first collection with resolving references, `missing-for-submission` annotation only when not ready).

The Standards Alignment panel exposes **Export FHIR artifacts** buttons (Questionnaire, QuestionnaireResponse, PAS Bundle) that download the JSON for the current review via these endpoints.

---

## CRD as a CDS Hooks service (Da Vinci CRD)

The policy-pack matcher is also exposed as a real **CDS Hooks** service — the same interface an EHR calls at order time to discover coverage requirements ([PRD Epic 3](./prd-cms0057-davinci.md)). It is mounted at the **application root** (not under `/api`) because CDS Hooks clients expect discovery at `{baseUrl}/cds-services`. Handler logic lives in [`backend/app/services/standards/crd_hooks.py`](../backend/app/services/standards/crd_hooks.py); models in [`backend/app/models/cds_hooks.py`](../backend/app/models/cds_hooks.py).

| Endpoint | Purpose |
|---|---|
| `GET /cds-services` | Discovery document advertising two services: `prior-auth-crd` (hook `order-select`) and `prior-auth-crd-sign` (hook `order-sign`), each with a `coverage` prefetch template |
| `POST /cds-services/{service_id}` | Invoke a service with a CDS Hooks request; returns decision `cards` |

The handler walks the hook `context` (`draftOrders` bundle, `selections`) and `prefetch` for the requested service (`ServiceRequest`/`DeviceRequest`/`MedicationRequest` codes → procedure/diagnosis) and coverage (`Coverage.payor.display` → payer, `Coverage.class[plan]` → plan), then calls `match_policy_pack()`. Cards:

- **Match + PA required** → a `warning` card: "Prior authorization required — {policy}", routing channel and requirement count in the detail, and an `absolute` link to that pack's `/questionnaire-package` (the DTR hand-off). The link's host is taken from the incoming request, so it points back at whatever served the CDS call.
- **Match + PA not required** → an `info` card.
- **No match** → an `info` card noting the runtime Medicare LCD/NCD fallback — never a false assertion of certainty.

`build_crd_response()` never raises: malformed input yields a safe non-blocking `info` card, so the service cannot 500. When `ENABLE_CRD_HOOKS=false`, discovery returns an empty `services` list and invocation returns `503`.

Verified offline by `cd backend && python scripts/check_crd_hooks.py` (discovery shape; CPT 22612 / UHC Commercial → warning card with a questionnaire-package link; CPT 99213 / Aetna → info card; malformed input → safe card).

---

## UI — Standards Alignment panel

A new [`StandardsPanel`](../frontend/components/standards-panel.tsx) renders under the Submission Readiness header in the assessment tab:

- **CRD** — "Prior authorization required" + routing-channel badges.
- **DTR** — a collapsible requirement checklist (defaults open) with a `{met}/{total} met` chip; each row shows a MET / INSUFFICIENT / MISSING status, confidence, evidence, and the gap action.
- **PAS** — a "PAS package ready / Not yet PAS-ready" badge, submission channel, the package summary, and a before-submission checklist.

It reuses the existing card/badge design, is collapsible so the baseline demo stays simple, and clearly labels the data as a reviewed policy pack (no live payer API implied). When no pack matches it shows a compact "runtime Medicare LCD/NCD search applies" note. The Debug Console labels the new `standards` trace phase *"Standards Alignment (CRD/DTR/PAS)"*.

---

## API & configuration

### Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/review`, `POST /api/review/stream` | Unchanged contract; response now includes an optional `standards` block |
| `GET /api/policy-packs` | List loaded packs + resolved directory (fast, agent-free — confirms a pack shipped in the image) |
| `GET /api/policy-packs/{policy_set_id}` | Full pack detail |
| `GET /api/policy-packs/{policy_set_id}/questionnaire` | FHIR R4 `Questionnaire` (DTR-shaped) built from the pack |
| `GET /api/policy-packs/{policy_set_id}/questionnaire-package` | DTR `$questionnaire-package`-shaped `Parameters` (Questionnaire + rules `Library`) |
| `GET /api/review/{request_id}/dtr/questionnaire-response` | Pre-populated `QuestionnaireResponse` for a completed review (404 when no pack matched) |
| `GET /api/review/{request_id}/pas/bundle` | PAS-shaped request `Bundle` for a completed review (export only; 404 when no pack matched) |

### Feature flags ([`backend/app/config.py`](../backend/app/config.py))

| Variable | Default | Effect |
|---|---|---|
| `ENABLE_STANDARDS_LAYER` | `true` | Master switch for the CRD/DTR/PAS block |
| `ENABLE_POLICY_PACKS` | `true` | Enable pack matching |
| `ENABLE_PAS_PREPARE` | `true` | Build the PAS package preview (never submits) |
| `POLICY_PACKS_DIR` | *(unset)* | Override the packs directory (defaults to bundled `backend/policy_packs`) |
| `ENABLE_FHIR_ARTIFACTS` | `true` | Serve the FHIR artifact endpoints (Questionnaire / package / QuestionnaireResponse / PAS bundle); `503` when off |
| `FHIR_CANONICAL_BASE` | `https://prior-auth.example/fhir` | Canonical base URL stamped into generated FHIR artifacts |
| `ENABLE_CRD_HOOKS` | `true` | Serve the CRD CDS Hooks service at `/cds-services`; empty discovery + `503` invocation when off |

---

## What changed (file map)

**Backend**
- `app/models/standards.py` — all standards + policy-pack models *(new)*
- `app/services/policy_store/{loader,matcher}.py` — load + match packs *(new)*
- `app/services/standards/evaluator.py` — CRD/DTR/PAS builder + demo-NPI fix *(new)*
- `app/routers/standards.py` — `GET /api/policy-packs` *(new)*
- `app/agents/orchestrator.py` — CRD match, requirement injection, demo-NPI fix, standards assessment + trace phase *(edited)*
- `app/models/schemas.py` — `standards` on `ReviewResponse`; `app/routers/review.py`, `app/main.py`, `app/config.py`, `Dockerfile` *(edited)*
- `policy_packs/uhc-commercial-lumbar-fusion/policy_set.json` — flagship pack *(new)*
- `scripts/{check_policy_store,check_standards,e2e_standards}.py` — verification *(new)*

**Agents** — `agents/compliance/.../SKILL.md`, `agents/coverage/.../SKILL.md` *(additive requirement-aware sections)*

**Frontend** — `lib/types.ts` (standards types), `components/standards-panel.tsx` *(new)*, `components/review-dashboard.tsx` + `components/debug-console.tsx` *(edited)*

---

## Verification

| Check | Command | Result |
|---|---|---|
| Pack load + match | `cd backend && python scripts/check_policy_store.py` | PASS |
| Standards service (offline) | `cd backend && python scripts/check_standards.py` | PASS |
| FHIR artifacts (offline) | `cd backend && python scripts/check_fhir_artifacts.py` | PASS |
| CRD CDS Hooks (offline) | `cd backend && python scripts/check_crd_hooks.py` | PASS |
| Frontend types | `cd frontend && npx tsc --noEmit` | clean |
| Pack shipped in image | `GET /api/policy-packs` | `count:1`, `/app/policy_packs` |
| Full pipeline (live) | `python scripts/e2e_standards.py <backendUrl>` | PASS |

**Flagship live result** (UHC Commercial lumbar fusion): PA required → UHC portal · DTR **6/8** met · gaps = PT discharge summary + smoking-cessation · PAS **not ready** · provider **VERIFIED** · Coverage agent emitted 11 pack-sourced criteria.

---

## Deployment notes

- **`azd up` is required** to pick up backend changes and (for the SKILL.md changes) rebuild + re-register the hosted agents — the agent build/register runs in the `postprovision` hook, which fires on `azd up`/`azd provision`, **not** `azd deploy`.
- If `azd up` is run from a **separate clone** (e.g. a Codespace), that clone must `git pull origin main` **before** deploying, or it ships stale code. Confirm with `git log --oneline -1`.
- Policy packs must live under `backend/` to ship in the image (see [Policy packs](#policy-packs)).

---

## Boundaries & non-goals

This phase deliberately does **not**:

- Call live CMS-0057 CRD / DTR / PAS endpoints (derived locally from reviewed packs).
- Implement full CQL execution (human-readable descriptions/criteria are authoritative).
- Submit real prior auth requests, or make payer approve/deny determinations.
- Implement X12 278 mapping or a production EHR/FHIR ingestion pipeline.

Pack content shipped here is synthetic demo data. Medicare LCD/NCD runtime search remains the fallback when no pack matches.

## Future direction

The FHIR-aligned model is the on-ramp to: real CRD (replace runtime search with a payer coverage-requirements feed), real DTR (consume payer `Questionnaire`/CQL at runtime), real PAS submission + status tracking, and Agent-Creator-generated payer-specific clinician assistants.

The staged plan for getting there — PAS `Bundle` export, a CRD CDS Hooks service, a synthetic payer sandbox, PA lifecycle timers (72h/7d), denial/appeal handling, metrics, and FHIR-native intake — is specified epic-by-epic in the [CMS-0057 / Da Vinci PRD](./prd-cms0057-davinci.md). Epic 1 (DTR FHIR artifacts) is implemented; see [FHIR artifacts](#fhir-artifacts-da-vinci-dtr).
