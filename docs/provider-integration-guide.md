# Provider Integration Guide

## Who this guide is for

This guide is written for provider executives, clinical informatics leaders, revenue cycle leaders, prior authorization managers, and integration teams who want to understand how this solution can fit into existing provider workflows.

## What problem this solves

Most provider organizations do not struggle with making payer decisions. They struggle with **assembling complete, payer-ready prior authorization packets** from fragmented provider systems.

Typical pain points include:

- requested services arriving from multiple intake channels
- missing attachments or incomplete chart notes at the point of submission
- inconsistent documentation of failed treatment history or severity
- limited visibility into why a case is not yet ready to submit
- repeated rework after payer pend requests
- difficulty preserving a clean audit trail when a clinician revises the recommendation

This accelerator helps the provider team answer a practical operational question:

> **Is this prior authorization packet complete enough to submit, or what is still missing?**

## Where this fits in your current systems

This solution is intended to sit between the systems you already use and the final payer submission workflow.

| Existing provider system | Typical source data | How this accelerator fits |
|--------------------------|---------------------|---------------------------|
| EHR / EMR | Patient demographics, coverage, diagnoses, encounter notes, observations, orders, reports | Supplies the core clinical and demographic payload |
| Referral / authorization platform | Intake queues, service requests, scheduling context, authorization statuses | Triggers a readiness review before staff submit or resubmit a case |
| Revenue cycle / work queue tools | Assigned ownership, submission tracking, payer follow-up tasks | Receives readiness status, documentation gaps, and next-step outputs |
| Document management / fax ingestion | Referral packets, payer forms, scanned records, outside clinical notes | Feeds attachment metadata or document bundles into the review workflow |
| Payer portal / ePA connector | Final submission channel | Remains the system of record for actual payer submission unless you build a deeper integration |

## Minimum data required

At a minimum, the accelerator works best when the provider can supply:

- patient name and date of birth
- billing or submitting provider NPI
- requested diagnosis codes
- requested procedure / HCPCS codes
- clinical notes or a provider-authored narrative
- insurance or member identifier when available

For more operationally realistic integrations, it is also useful to send:

- ordering provider name and NPI
- rendering provider specialty
- servicing facility or department
- payer and plan or product name
- urgency / expedited flag
- place of service
- attachment or note-type metadata
- prior treatment history

## Recommended integration patterns

### 1. FHIR-based integration

**Best fit:** Health systems with modern EHR API access and an interoperability team comfortable with FHIR.

**Typical data sources:**
- `Patient`
- `Coverage`
- `Encounter`
- `Condition`
- `Observation`
- `Procedure`
- `ServiceRequest`
- `DocumentReference`
- `DiagnosticReport`
- `Practitioner` / `PractitionerRole`

**How it works:**
- Pull demographic, coverage, and ordering context from the EHR.
- Pull note/document references and selected clinical evidence.
- Build a prior auth intake payload and submit it to `POST /api/review` or `POST /api/review/stream`.
- Write the returned readiness summary back into the staff work queue or chart workflow.

**Tradeoffs:**
- Strongest long-term pattern for discrete interoperability.
- Requires more up-front mapping work and governance around which FHIR resources are authoritative.

### 2. HL7 v2-triggered workflow

**Best fit:** Organizations that already run interface-engine workflows and need event-driven intake before broad FHIR coverage exists.

**Typical triggers:**
- ADT events for new visits or admissions
- SIU events for scheduled services
- order-triggered messages tied to imaging, surgery, infusion, or DME workflows

**How it works:**
- Use HL7 v2 or interface-engine logic to trigger a prior-auth-required work item.
- Enrich the event with chart content from downstream APIs or document stores.
- Submit the normalized packet to the accelerator for review.

**Tradeoffs:**
- Fits existing interface-engine operations well.
- Message payloads are often not sufficient on their own; you usually still need chart lookups for documentation and attachments.

### 3. Batch or document-ingestion workflow

**Best fit:** Organizations that still receive outside records, payer forms, or referral packets by fax, PDF, or document queue.

**How it works:**
- Ingest the document packet into a staging area.
- Capture metadata such as note types, service requested, payer, and ordering provider.
- Route the packet to the accelerator to identify missing evidence and packet readiness.

**Tradeoffs:**
- Easiest bridge pattern when provider data is fragmented.
- Produces less structured input unless paired with OCR, indexing, or chart enrichment.

### 4. Webhook or callback into work queues

**Best fit:** Revenue cycle and prior-auth teams that need results surfaced inside an existing queue or referral platform.

**How it works:**
- Your integration layer submits a case for review.
- The response updates a work queue status such as `Ready to submit`, `Needs clinical addendum`, or `Missing attachment`.
- Staff use the audit trail and documentation gaps to decide whether to submit, resubmit, or escalate.

**Tradeoffs:**
- Operationally powerful because it fits the staff workflow.
- Depends on the maturity of the queue or referral platform API layer.

## Common provider workflow patterns

### Review before submission

Use when a new PA-required case first arrives in a queue.

1. Intake team assembles the initial packet from EHR, referral, and document sources.
2. The packet is reviewed by the accelerator.
3. Staff receive a readiness summary and documentation gap list.
4. Only cases marked as ready move to payer submission.

### Resubmit after missing documentation

Use when a payer pend or internal QA step identifies missing support.

1. Staff attach updated reports, treatment history, or provider addenda.
2. The case is re-run through the accelerator.
3. The updated packet replaces the prior internal work-queue status.
4. Staff resubmit with a more complete package.

### Human override with audit trail

Use when a physician reviewer or medical director disagrees with the draft assessment.

1. A clinician reviews the AI-produced summary and the supporting evidence.
2. The clinician revises the recommendation with rationale.
3. The system preserves the override, updated audit record, and notification output.
4. Staff can use that record for payer follow-up, peer-to-peer preparation, or appeal support.

## Phased rollout approach

### Phase 1: Narrow pilot

Start with a specialty and service line where documentation patterns are repeatable, such as:
- advanced imaging
- infusion authorizations
- spine surgery
- DME oxygen workflows

Goals:
- validate intake field mapping
- confirm provider staff trust the readiness outputs
- measure documentation-gap reduction and time saved

### Phase 2: Operational embedding

Add:
- queue routing
- EHR or referral-system lookups
- packet resubmission workflow
- staff ownership and escalation rules

Goals:
- reduce first-pass defects
- standardize staff follow-up
- create consistent medical director escalation paths

### Phase 3: Production-scale integration

Add:
- enterprise identity and RBAC
- persistent storage and downstream analytics
- callback/webhook integrations
- provider-specific specialty rules and letter templates

Goals:
- broader service-line adoption
- richer audit and denial-prevention reporting
- operationalized support model across IT, clinical, and revenue cycle teams

## Operational ownership model

| Function | Typical owner | Responsibilities |
|----------|---------------|------------------|
| Clinical content quality | Physician reviewer / specialty leader | Validate specialty-specific requirements and escalation logic |
| Prior auth operations | PA manager / utilization review lead | Own intake quality, work queues, resubmission workflow, and staff SOPs |
| Revenue cycle operations | RCM leadership | Align outputs with payer follow-up, denial prevention, and reporting |
| IT / integration | Interface engine / digital health / interoperability team | Build and maintain FHIR, HL7, queue, and document integrations |
| Security / compliance | Security and privacy teams | Review PHI handling, access controls, retention, and environment hardening |

## Security and PHI handling expectations

This project processes PHI and should be treated like any other provider workflow component handling clinical and financial data.

Recommended controls for production use include:
- SSO and role-based access control for staff users
- encryption at rest and in transit
- PHI-minimizing payload design where practical
- audit logging for submission, revision, and override actions
- documented retention rules for generated audit PDFs and letters
- network and environment isolation appropriate to your organization
- Business Associate Agreement and compliance review where required

The repository already emphasizes Azure-based keyless authentication and managed identity. For production deployment, providers should extend this with organizational access controls, monitoring, and governance standards.

## Example provider outputs to evaluate

### Example: incomplete packet summary

- **Status:** Needs review
- **Primary reason:** clinical packet references imaging and PT history, but attachment metadata does not include the MRI report or PT discharge summary
- **Operational next step:** return to the assigned PA coordinator work queue for attachment completion before submission

### Example: ready-to-submit packet summary

- **Status:** Ready to submit
- **Primary reason:** patient, provider, code validation, documented failed treatment history, and payer-policy criteria are all represented in the packet
- **Operational next step:** move directly to payer portal submission and preserve the generated audit artifact in the authorization record

### Example: physician override summary

- **Status:** Staff revised
- **Primary reason:** medical director determined the packet was submission-ready after reviewing external documentation not yet indexed in the chart
- **Operational next step:** submit with the override rationale attached to the case record for payer follow-up if needed

## Provider-specific customization guidance

Most organizations should expect to customize at least four areas:

1. **Payer rules**
   - commercial plan differences
   - Medicare Advantage variations
   - local submission requirements and attachments

2. **Specialty-specific documentation expectations**
   - oncology biomarker and regimen history
   - pulmonology imaging and pulmonary function test expectations
   - orthopedics conservative treatment duration and functional limitation documentation
   - cardiology stress testing, imaging, or rhythm-monitor prerequisites

3. **Local communication templates**
   - branded staff letters
   - internal work-queue statuses
   - escalation reasons and service-line routing

4. **Escalation thresholds**
   - when a PA coordinator can submit directly
   - when a case requires utilization review nurse signoff
   - when a physician reviewer or medical director must intervene

## Related repository resources

- [README](../README.md)
- [Architecture](./architecture.md)
- [API Reference](./api-reference.md)
- [Extending the Application](./extending.md)
- [Production Migration](./production-migration.md)
