// =============================================================================
// Single source of truth for the walkthrough video + narration script.
//
// Both the Playwright director (record-walkthrough.mjs) and the script
// generator (gen-script.mjs) import this file, so the burned-in captions and
// the written narration never drift.
//
// Captions are deliberately QUALITATIVE: the agents reason live, so the exact
// confidence/criteria numbers vary run to run. The deterministic beat — the
// provider's NPI fails Gate 1, so the packet is held for review — is what the
// narrative relies on.
// =============================================================================

export const TARGET_URL =
  process.env.TARGET_URL ||
  "https://ca-frontend-zjaacdjlovvhc.happyhill-acd426b3.eastus2.azurecontainerapps.io/";

// Ordered chapters. `card` chapters render a full-screen title card in the video
// and become the cut points for the teaser.
export const CHAPTERS = [
  { key: "intro",       title: "Provider Prior Authorization",     sub: "A guided walkthrough — from intake to a decision-ready report" },
  { key: "intake",      title: "1 · Build the packet",             sub: "Patient, provider, codes, and clinical notes" },
  { key: "assess",      title: "2 · Run the assessment",           sub: "One button · four specialist reviewers" },
  { key: "verdict",     title: "3 · The verdict",                  sub: "A clear, evidence-backed readiness call" },
  { key: "reviewers",   title: "4 · Inside each reviewer",         sub: "Drill into what every specialist found" },
  { key: "signoff",     title: "5 · Human sign-off",               sub: "The tool advises — a person decides" },
  { key: "report",      title: "6 · The report",                   sub: "A shareable, file-ready PDF" },
  { key: "underhood",   title: "7 · Under the hood",               sub: "For technical viewers: agents & tool calls" },
  { key: "close",       title: "Prepared, not decided",            sub: "An AI-assisted draft — human review required before submission" },
];

export const SCENES = [
  // --- Intro ---
  { id: "intro", chapter: "intro", holdMs: 4200,
    action: "Title card.",
    caption: "From intake to a decision-ready report.",
    narration: "This is the Provider Prior Authorization assistant. In a few minutes you'll see how it takes a prior-auth packet, reviews it with a team of four specialists, and produces a decision-ready report — without anyone writing code." },

  // --- 1 · Build the packet ---
  { id: "intake-overview", chapter: "intake", holdMs: 5200,
    action: "Show the New Provider Prior Auth Intake screen.",
    caption: "Every prior auth starts as a packet: patient, provider, codes, and notes.",
    narration: "Everything starts on the intake screen. A coordinator captures the patient, the provider, the diagnosis and procedure codes, and the clinical notes — the same things you'd assemble before sending a packet to a payer." },
  { id: "intake-sample", chapter: "intake", holdMs: 5000,
    action: "Open the sample-case picker; reveal the four built-in cases.",
    caption: "Build it by hand — or load one of four realistic sample cases.",
    narration: "You can fill it in by hand, or load one of four realistic sample cases — pulmonology, oncology, orthopedics, and home oxygen." },
  { id: "intake-loaded", chapter: "intake", holdMs: 5000,
    action: "Select the Orthopedics case and click Load Sample.",
    caption: "Loaded: an orthopedic lumbar-fusion case for patient Thomas Reed.",
    narration: "We'll use the orthopedics case — an outpatient lumbar fusion for Thomas Reed. One click fills the whole packet." },
  { id: "intake-advanced", chapter: "intake", holdMs: 5200,
    action: "Toggle Show EHR/FHIR-Style Intake; scroll the advanced fields.",
    caption: "Advanced intake mirrors the discrete fields from an EHR or referral system.",
    narration: "An advanced section mirrors the structured fields an EHR or referral system would carry — ordering provider, payer and plan, place of service, attached documents, and prior-treatment history." },
  { id: "intake-codes", chapter: "intake", holdMs: 4800,
    action: "Scroll to the ICD-10 and CPT/HCPCS code rows.",
    caption: "Diagnosis (ICD-10) and procedure (CPT/HCPCS) codes drive the review.",
    narration: "The diagnosis and procedure codes are central — they drive both the coding checks and the policy match." },

  // --- 2 · Run the assessment ---
  { id: "assess-click", chapter: "assess", holdMs: 3800,
    action: "Click Assess Prior Auth Packet.",
    caption: "One button starts the review.",
    narration: "Now we press Assess. From here it's automatic." },
  { id: "assess-preflight", chapter: "assess", holdMs: 6000,
    action: "Scroll to the live progress tracker (preflight).",
    caption: "First, a quick preflight checks the procedure-code format.",
    narration: "First a quick preflight validates the procedure-code format, catching obvious typos before the specialists run." },
  { id: "assess-phase1", chapter: "assess", holdMs: 7500,
    action: "Progress: Phase 1 (parallel).",
    caption: "Two reviewers run in parallel: Documentation Completeness and Clinical Evidence.",
    narration: "Then two reviewers work in parallel. Documentation Completeness checks the packet is whole; Clinical Evidence validates the codes and pulls the medical story out of the notes." },
  { id: "assess-phase2", chapter: "assess", holdMs: 7500,
    action: "Progress: Phase 2.",
    caption: "Next, Policy Matching verifies the provider and matches payer policy.",
    narration: "Next, Policy Matching verifies the provider against the national registry and matches the case to coverage policy." },
  { id: "assess-phase3", chapter: "assess", holdMs: 7500,
    action: "Progress: Phase 3 / finalizing.",
    caption: "Finally, Submission Readiness makes the call through three strict gates.",
    narration: "Finally, Submission Readiness weighs everything and makes the call through three strict gates. The whole run takes about ninety seconds." },

  // --- 3 · The verdict ---
  { id: "verdict", chapter: "verdict", holdMs: 6500,
    action: "Show the Submission Readiness Assessment header (verdict + confidence + summary).",
    caption: "The verdict: Needs Review — a strong clinical case, but one real blocker.",
    narration: "And here's the verdict: Needs Review. The clinical case is strong and the coding lines up with policy — but one real blocker is holding it back, which we'll see in a moment." },
  { id: "verify-checks", chapter: "verdict", holdMs: 5500,
    action: "Scroll to Verification Checks.",
    caption: "Verification Checks — every code, provider, and policy lookup that ran.",
    narration: "Verification Checks lists every live lookup the system ran — code validations, the provider lookup, and the coverage searches." },
  { id: "requirements", chapter: "verdict", holdMs: 5500,
    action: "Scroll to Payer Policy Requirements (Met / Not Yet Met).",
    caption: "Payer requirements, split into Met and Not Yet Met.",
    narration: "Payer requirements are split into what's met and what isn't yet — so you can see exactly where the packet stands." },
  { id: "gaps", chapter: "verdict", holdMs: 5500,
    action: "Scroll to Documentation Gaps / Action Required.",
    caption: "A prioritized to-do list — what's Required vs. Recommended.",
    narration: "Documentation Gaps turn that into a prioritized to-do list — what's required before submitting, and what's merely recommended." },
  { id: "policy-refs", chapter: "verdict", holdMs: 5000,
    action: "Scroll to Payer Policy References.",
    caption: "The exact Medicare policies consulted — LCDs, NCDs, and articles.",
    narration: "It cites the exact policies it consulted, so the reasoning is traceable." },
  { id: "rationale", chapter: "verdict", holdMs: 5200,
    action: "Scroll to Clinical Evidence Rationale.",
    caption: "And a plain-language rationale for the decision.",
    narration: "And it explains the decision in plain language, the way a reviewer would note it." },

  // --- 4 · Inside each reviewer ---
  { id: "agent-doc", chapter: "reviewers", holdMs: 6000,
    action: "Agent Details → Doc. Completeness tab.",
    caption: "Documentation Completeness — the full checklist, every field verified.",
    narration: "You can drill into each reviewer. Documentation Completeness shows the full checklist, with every field verified or flagged." },
  { id: "agent-clinical", chapter: "reviewers", holdMs: 6500,
    action: "Agent Details → Clinical Evidence tab; show validation + extraction.",
    caption: "Clinical Evidence — codes validated, and the story extracted from the notes.",
    narration: "Clinical Evidence shows the diagnosis and procedure codes validated as real and billable, plus the structured clinical evidence pulled straight from the notes." },
  { id: "agent-policy", chapter: "reviewers", holdMs: 7000,
    action: "Agent Details → Policy Matching tab; show provider verification + per-code matrix.",
    caption: "Policy Matching — provider verification, the per-code coverage matrix, and criteria.",
    narration: "Policy Matching is where this case turns. Here's the provider verification — and it's unverified — alongside the per-code coverage matrix and each policy criterion." },
  { id: "agent-submission", chapter: "reviewers", holdMs: 7000,
    action: "Agent Details → Submission Readiness tab; show the three-gate pipeline.",
    caption: "Submission Readiness — the three-gate pipeline. Gate 1 (provider) fails, so it's held.",
    narration: "And Submission Readiness shows the three-gate pipeline. Gate one — the provider credential check — fails, because the NPI couldn't be verified. A failed gate one holds the packet, no matter how strong the rest is. That's the blocker." },

  // --- 5 · Human sign-off ---
  { id: "signoff-revise", chapter: "signoff", holdMs: 5800,
    action: "Decision panel: enter reviewer name; open Revise Assessment to show override + rationale.",
    caption: "A human stays in control — revise the assessment with a documented rationale…",
    narration: "Nothing is final until a person signs off. A reviewer can revise the assessment, recording a rationale for the change…" },
  { id: "signoff-accept", chapter: "signoff", holdMs: 5500,
    action: "Cancel revise; click Accept AI Assessment.",
    caption: "…or accept it. Either way, it's logged with the reviewer's name and time.",
    narration: "…or accept it as-is. Either way it's recorded — who decided, when, and on what evidence." },
  { id: "signoff-letter", chapter: "signoff", holdMs: 5200,
    action: "Show Authorization Recorded + Download Provider Letter.",
    caption: "A provider letter is generated, ready to download.",
    narration: "On sign-off, a provider letter is generated and ready to download." },

  // --- 6 · The report ---
  { id: "report", chapter: "report", holdMs: 6000,
    action: "Scroll to Submission Readiness Report card; click Download Report (capture PDF).",
    caption: "Download the full 8-section Submission Readiness Report as a PDF.",
    narration: "And the headline deliverable: the full eight-section Submission Readiness Report, downloaded as a PDF for the chart, the surgeon, or your audit trail." },

  // --- 7 · Under the hood ---
  { id: "debug-intro", chapter: "underhood", holdMs: 6000,
    action: "Switch to the Debug Console top tab.",
    caption: "For technical viewers: the Debug Console shows the agents and tool calls behind the result.",
    narration: "For the technically curious, the Debug Console exposes everything behind the result — the agents and the tool calls they made." },
  { id: "debug-timeline", chapter: "underhood", holdMs: 5200,
    action: "Debug Console → Timeline.",
    caption: "Timeline — every model and tool call, with timing.",
    narration: "The Timeline lays out every model and tool call with its duration." },
  { id: "debug-events", chapter: "underhood", holdMs: 6000,
    action: "Debug Console → Events; open an event (Event / Request / Response).",
    caption: "Events — step through the raw, privacy-redacted request and response data.",
    narration: "The Events inspector lets you step through each call and see the raw request and response data, with patient details redacted." },
  { id: "debug-graph", chapter: "underhood", holdMs: 5000,
    action: "Debug Console → Graph.",
    caption: "Graph — which reviewer called which tools.",
    narration: "The Graph shows which reviewer called which tools." },
  { id: "debug-foundry", chapter: "underhood", holdMs: 5500,
    action: "Debug Console → Foundry.",
    caption: "Foundry — live container logs, traces, and App Insights spans.",
    narration: "And the Foundry tab links straight through to live container logs, distributed traces, and Application Insights spans." },

  // --- Close ---
  { id: "close", chapter: "close", holdMs: 5800,
    action: "Closing title card.",
    caption: "An AI-assisted draft. Human review is required before submission.",
    narration: "One last thing: this prepares a submission — it is not a coverage decision, and a human review is always required before anything goes to a payer. That's the walkthrough." },
];

// Helper: look up a scene by id (used by the director).
export function scene(id) {
  const s = SCENES.find((x) => x.id === id);
  if (!s) throw new Error(`Unknown scene id: ${id}`);
  return s;
}

export function chapter(key) {
  const c = CHAPTERS.find((x) => x.key === key);
  if (!c) throw new Error(`Unknown chapter key: ${key}`);
  return c;
}
