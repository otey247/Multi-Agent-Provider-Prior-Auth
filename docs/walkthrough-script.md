# Walkthrough — Narration Script

*Auto-generated from `scripts/demo/scenes.mjs` (run `node scripts/demo/gen-script.mjs`).
The on-screen captions in the video are the `Caption` lines below; the `Voiceover`
lines are the fuller narration for a human reader or text-to-speech. Captions stay
qualitative because the agents reason live — the deterministic beat is that the
provider's NPI fails Gate 1, so the packet is held for review.*

- **Recording target:** `https://ca-frontend-zjaacdjlovvhc.happyhill-acd426b3.eastus2.azurecontainerapps.io/`
- **Videos:** `docs/videos/walkthrough-deepdive.mp4` (full) · `docs/videos/walkthrough-teaser.mp4` (~3 min)
- **Approx. total caption time:** 177.4s (plus the ~90s live assessment run)

---

## Deep-dive script

### Provider Prior Authorization

*A guided walkthrough — from intake to a decision-ready report*

- **[4.2s] intro** — _Title card._
  - **Caption:** From intake to a decision-ready report.
  - **Voiceover:** This is the Provider Prior Authorization assistant. In a few minutes you'll see how it takes a prior-auth packet, reviews it with a team of four specialists, and produces a decision-ready report — without anyone writing code.

### 1 · Build the packet

*Patient, provider, codes, and clinical notes*

- **[5.2s] intake-overview** — _Show the New Provider Prior Auth Intake screen._
  - **Caption:** Every prior auth starts as a packet: patient, provider, codes, and notes.
  - **Voiceover:** Everything starts on the intake screen. A coordinator captures the patient, the provider, the diagnosis and procedure codes, and the clinical notes — the same things you'd assemble before sending a packet to a payer.
- **[5s] intake-sample** — _Open the sample-case picker; reveal the four built-in cases._
  - **Caption:** Build it by hand — or load one of four realistic sample cases.
  - **Voiceover:** You can fill it in by hand, or load one of four realistic sample cases — pulmonology, oncology, orthopedics, and home oxygen.
- **[5s] intake-loaded** — _Select the Orthopedics case and click Load Sample._
  - **Caption:** Loaded: an orthopedic lumbar-fusion case for patient Thomas Reed.
  - **Voiceover:** We'll use the orthopedics case — an outpatient lumbar fusion for Thomas Reed. One click fills the whole packet.
- **[5.2s] intake-advanced** — _Toggle Show EHR/FHIR-Style Intake; scroll the advanced fields._
  - **Caption:** Advanced intake mirrors the discrete fields from an EHR or referral system.
  - **Voiceover:** An advanced section mirrors the structured fields an EHR or referral system would carry — ordering provider, payer and plan, place of service, attached documents, and prior-treatment history.
- **[4.8s] intake-codes** — _Scroll to the ICD-10 and CPT/HCPCS code rows._
  - **Caption:** Diagnosis (ICD-10) and procedure (CPT/HCPCS) codes drive the review.
  - **Voiceover:** The diagnosis and procedure codes are central — they drive both the coding checks and the policy match.

### 2 · Run the assessment

*One button · four specialist reviewers*

- **[3.8s] assess-click** — _Click Assess Prior Auth Packet._
  - **Caption:** One button starts the review.
  - **Voiceover:** Now we press Assess. From here it's automatic.
- **[6s] assess-preflight** — _Scroll to the live progress tracker (preflight)._
  - **Caption:** First, a quick preflight checks the procedure-code format.
  - **Voiceover:** First a quick preflight validates the procedure-code format, catching obvious typos before the specialists run.
- **[7.5s] assess-phase1** — _Progress: Phase 1 (parallel)._
  - **Caption:** Two reviewers run in parallel: Documentation Completeness and Clinical Evidence.
  - **Voiceover:** Then two reviewers work in parallel. Documentation Completeness checks the packet is whole; Clinical Evidence validates the codes and pulls the medical story out of the notes.
- **[7.5s] assess-phase2** — _Progress: Phase 2._
  - **Caption:** Next, Policy Matching verifies the provider and matches payer policy.
  - **Voiceover:** Next, Policy Matching verifies the provider against the national registry and matches the case to coverage policy.
- **[7.5s] assess-phase3** — _Progress: Phase 3 / finalizing._
  - **Caption:** Finally, Submission Readiness makes the call through three strict gates.
  - **Voiceover:** Finally, Submission Readiness weighs everything and makes the call through three strict gates. The whole run takes about ninety seconds.

### 3 · The verdict

*A clear, evidence-backed readiness call*

- **[6.5s] verdict** — _Show the Submission Readiness Assessment header (verdict + confidence + summary)._
  - **Caption:** The verdict: Needs Review — a strong clinical case, but one real blocker.
  - **Voiceover:** And here's the verdict: Needs Review. The clinical case is strong and the coding lines up with policy — but one real blocker is holding it back, which we'll see in a moment.
- **[5.5s] verify-checks** — _Scroll to Verification Checks._
  - **Caption:** Verification Checks — every code, provider, and policy lookup that ran.
  - **Voiceover:** Verification Checks lists every live lookup the system ran — code validations, the provider lookup, and the coverage searches.
- **[5.5s] requirements** — _Scroll to Payer Policy Requirements (Met / Not Yet Met)._
  - **Caption:** Payer requirements, split into Met and Not Yet Met.
  - **Voiceover:** Payer requirements are split into what's met and what isn't yet — so you can see exactly where the packet stands.
- **[5.5s] gaps** — _Scroll to Documentation Gaps / Action Required._
  - **Caption:** A prioritized to-do list — what's Required vs. Recommended.
  - **Voiceover:** Documentation Gaps turn that into a prioritized to-do list — what's required before submitting, and what's merely recommended.
- **[5s] policy-refs** — _Scroll to Payer Policy References._
  - **Caption:** The exact Medicare policies consulted — LCDs, NCDs, and articles.
  - **Voiceover:** It cites the exact policies it consulted, so the reasoning is traceable.
- **[5.2s] rationale** — _Scroll to Clinical Evidence Rationale._
  - **Caption:** And a plain-language rationale for the decision.
  - **Voiceover:** And it explains the decision in plain language, the way a reviewer would note it.

### 4 · Inside each reviewer

*Drill into what every specialist found*

- **[6s] agent-doc** — _Agent Details → Doc. Completeness tab._
  - **Caption:** Documentation Completeness — the full checklist, every field verified.
  - **Voiceover:** You can drill into each reviewer. Documentation Completeness shows the full checklist, with every field verified or flagged.
- **[6.5s] agent-clinical** — _Agent Details → Clinical Evidence tab; show validation + extraction._
  - **Caption:** Clinical Evidence — codes validated, and the story extracted from the notes.
  - **Voiceover:** Clinical Evidence shows the diagnosis and procedure codes validated as real and billable, plus the structured clinical evidence pulled straight from the notes.
- **[7s] agent-policy** — _Agent Details → Policy Matching tab; show provider verification + per-code matrix._
  - **Caption:** Policy Matching — provider verification, the per-code coverage matrix, and criteria.
  - **Voiceover:** Policy Matching is where this case turns. Here's the provider verification — and it's unverified — alongside the per-code coverage matrix and each policy criterion.
- **[7s] agent-submission** — _Agent Details → Submission Readiness tab; show the three-gate pipeline._
  - **Caption:** Submission Readiness — the three-gate pipeline. Gate 1 (provider) fails, so it's held.
  - **Voiceover:** And Submission Readiness shows the three-gate pipeline. Gate one — the provider credential check — fails, because the NPI couldn't be verified. A failed gate one holds the packet, no matter how strong the rest is. That's the blocker.

### 5 · Human sign-off

*The tool advises — a person decides*

- **[5.8s] signoff-revise** — _Decision panel: enter reviewer name; open Revise Assessment to show override + rationale._
  - **Caption:** A human stays in control — revise the assessment with a documented rationale…
  - **Voiceover:** Nothing is final until a person signs off. A reviewer can revise the assessment, recording a rationale for the change…
- **[5.5s] signoff-accept** — _Cancel revise; click Accept AI Assessment._
  - **Caption:** …or accept it. Either way, it's logged with the reviewer's name and time.
  - **Voiceover:** …or accept it as-is. Either way it's recorded — who decided, when, and on what evidence.
- **[5.2s] signoff-letter** — _Show Authorization Recorded + Download Provider Letter._
  - **Caption:** A provider letter is generated, ready to download.
  - **Voiceover:** On sign-off, a provider letter is generated and ready to download.

### 6 · The report

*A shareable, file-ready PDF*

- **[6s] report** — _Scroll to Submission Readiness Report card; click Download Report (capture PDF)._
  - **Caption:** Download the full 8-section Submission Readiness Report as a PDF.
  - **Voiceover:** And the headline deliverable: the full eight-section Submission Readiness Report, downloaded as a PDF for the chart, the surgeon, or your audit trail.

### 7 · Under the hood

*For technical viewers: agents & tool calls*

- **[6s] debug-intro** — _Switch to the Debug Console top tab._
  - **Caption:** For technical viewers: the Debug Console shows the agents and tool calls behind the result.
  - **Voiceover:** For the technically curious, the Debug Console exposes everything behind the result — the agents and the tool calls they made.
- **[5.2s] debug-timeline** — _Debug Console → Timeline._
  - **Caption:** Timeline — every model and tool call, with timing.
  - **Voiceover:** The Timeline lays out every model and tool call with its duration.
- **[6s] debug-events** — _Debug Console → Events; open an event (Event / Request / Response)._
  - **Caption:** Events — step through the raw, privacy-redacted request and response data.
  - **Voiceover:** The Events inspector lets you step through each call and see the raw request and response data, with patient details redacted.
- **[5s] debug-graph** — _Debug Console → Graph._
  - **Caption:** Graph — which reviewer called which tools.
  - **Voiceover:** The Graph shows which reviewer called which tools.
- **[5.5s] debug-foundry** — _Debug Console → Foundry._
  - **Caption:** Foundry — live container logs, traces, and App Insights spans.
  - **Voiceover:** And the Foundry tab links straight through to live container logs, distributed traces, and Application Insights spans.

### Prepared, not decided

*An AI-assisted draft — human review required before submission*

- **[5.8s] close** — _Closing title card._
  - **Caption:** An AI-assisted draft. Human review is required before submission.
  - **Voiceover:** One last thing: this prepares a submission — it is not a coverage decision, and a human review is always required before anything goes to a payer. That's the walkthrough.

---

## Teaser script (~3 min)

The teaser is cut from the same recording — these are the beats it keeps (the
assessment wait is sped up, chapters cards are the cut points):

- **intro** — From intake to a decision-ready report.
- **intake-loaded** — Loaded: an orthopedic lumbar-fusion case for patient Thomas Reed.
- **assess-click** — One button starts the review.
- **assess-phase1** — Two reviewers run in parallel: Documentation Completeness and Clinical Evidence.
- **verdict** — The verdict: Needs Review — a strong clinical case, but one real blocker.
- **agent-policy** — Policy Matching — provider verification, the per-code coverage matrix, and criteria.
- **agent-submission** — Submission Readiness — the three-gate pipeline. Gate 1 (provider) fails, so it's held.
- **report** — Download the full 8-section Submission Readiness Report as a PDF.
- **close** — An AI-assisted draft. Human review is required before submission.

---

_Disclaimer (shown on the closing card): an AI-assisted draft for prior-auth preparation; not a payer coverage determination. Human review is required before submission._
