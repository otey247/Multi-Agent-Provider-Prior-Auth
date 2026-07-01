export interface PriorAuthRequest {
  patient_name: string;
  patient_dob: string;
  provider_npi: string;
  diagnosis_codes: string[];
  procedure_codes: string[];
  clinical_notes: string;
  insurance_id?: string;
  ordering_provider_name?: string;
  ordering_provider_npi?: string;
  rendering_provider_specialty?: string;
  servicing_facility?: string;
  payer_name?: string;
  payer_plan?: string;
  urgency?: "standard" | "urgent";
  place_of_service?: string;
  attached_note_types?: string[];
  prior_treatment_history?: string[];
}

export interface ToolResult {
  tool_name: string;
  status: "pass" | "fail" | "warning";
  detail: string;
}

// --- Per-agent result types ---

export interface AgentCheck {
  rule: string;
  result: "pass" | "fail" | "warning" | "info";
  detail: string;
}

export interface ChecklistItem {
  item: string;
  status: "complete" | "incomplete" | "missing";
  detail: string;
}

export interface ComplianceResult {
  agent_name: string;
  checks_performed: AgentCheck[];
  checklist: ChecklistItem[];
  overall_status: "complete" | "incomplete";
  missing_items: string[];
  additional_info_requests: string[];
  error?: string;
}

export interface DiagnosisValidation {
  code: string;
  valid: boolean;
  description: string;
  billable: boolean;
  hierarchy_note: string; // only when non-billable code has specific children
}

export interface ProcedureValidation {
  code: string;
  valid: boolean;
  description: string;
  source: string; // "orchestrator_preflight" or "unverified"
}

export interface ClinicalExtraction {
  chief_complaint: string;
  history_of_present_illness: string;
  prior_treatments: string[];
  severity_indicators: string[];
  functional_limitations: string[];
  diagnostic_findings: string[];
  duration_and_progression: string;
  medical_history_and_comorbidities?: string;
  extraction_confidence: number; // 0-100
}

export interface LiteratureReference {
  title: string;
  pmid: string;
  relevance: string;
}

export interface ClinicalTrialReference {
  nct_id: string;
  title: string;
  status: string;
  relevance: string;
}

export interface ClinicalResult {
  agent_name: string;
  checks_performed: AgentCheck[];
  diagnosis_validation: DiagnosisValidation[];
  procedure_validation: ProcedureValidation[];
  clinical_extraction?: ClinicalExtraction;
  literature_support: LiteratureReference[];
  clinical_trials: ClinicalTrialReference[];
  clinical_summary: string;
  tool_results: ToolResult[];
  error?: string;
}

export interface TaxonomyDetail {
  code: string;
  desc: string;
  primary: boolean;
  license: string;
  state: string;
}

export interface ProviderVerification {
  npi: string;
  name: string;
  specialty: string;
  status: "active" | "inactive" | "not_found";
  detail: string;
  credential?: string;
  taxonomies?: TaxonomyDetail[];
}

export interface PerCodeCoverage {
  code: string;
  code_type: "ICD10" | "HCPCS";
  status: "covered" | "non_covered" | "not_listed";
  policy_id: string;
}

export interface CoveragePolicy {
  policy_id: string;
  title: string;
  type: "LCD" | "NCD";
  relevant: boolean;
}

export interface CriterionAssessment {
  criterion: string;
  status: "MET" | "NOT_MET" | "INSUFFICIENT";
  confidence: number; // 0-100
  evidence: string[];
  notes: string;
  source: string;
  met: boolean;
}

export interface DocumentationGap {
  what: string;
  critical: boolean;
  request: string;
}

export interface CoverageResult {
  agent_name: string;
  checks_performed: AgentCheck[];
  provider_verification?: ProviderVerification;
  coverage_policies: CoveragePolicy[];
  criteria_assessment: CriterionAssessment[];
  coverage_criteria_met: string[];
  coverage_criteria_not_met: string[];
  policy_references: string[];
  coverage_limitations: string[];
  documentation_gaps: DocumentationGap[];
  per_code_coverage?: PerCodeCoverage[];
  tool_results: ToolResult[];
  error?: string;
}

export interface AgentResults {
  compliance?: ComplianceResult;
  clinical?: ClinicalResult;
  coverage?: CoverageResult;
}

export interface AuditTrail {
  data_sources: string[];
  review_started: string;
  review_completed: string;
  extraction_confidence: number;
  assessment_confidence: number;
  criteria_met_count: string; // "N/M" format
}

export interface SynthesisAuditTrail {
  gate_results?: Record<string, string>;          // "gate_1_provider": "PASS|FAIL"
  confidence_components?: Record<string, number>; // criteria_weight, criteria_score, ...
  agents_consulted?: string[];
}

// --- CMS-0057 / Da Vinci standards-aligned view (CRD/DTR/PAS) ---

export interface CrdDetermination {
  pa_required: boolean | null;
  routing_channel: string;
  delegated_vendor: string;
  determination_source: string; // "policy_pack" | "runtime_search" | "unknown"
  reasons: string[];
}

export interface RequirementEvaluation {
  requirement_id: string;
  description: string;
  requirement_type: string;
  required: boolean;
  conditional: boolean;
  status: "MET" | "INSUFFICIENT" | "MISSING" | "NOT_APPLICABLE";
  confidence: number; // 0-100
  evidence: string[];
  gap_action: string;
  source: string;
}

export interface DtrAssessment {
  source: string; // "policy_pack" | "runtime_search"
  questionnaire_id: string;
  requirements_total: number;
  requirements_met: number;
  requirement_evaluations: RequirementEvaluation[];
}

export interface PasPreview {
  pas_ready: boolean;
  portal_ready: boolean;
  submission_channel: string;
  missing_for_submission: string[];
  package_summary: Record<string, string>;
}

export interface StandardsAssessment {
  enabled: boolean;
  policy_pack_matched: boolean;
  policy_set_id: string;
  payer: string;
  plan: string;
  policy_name: string;
  policy_version: string;
  source_url: string;
  crd?: CrdDetermination | null;
  dtr?: DtrAssessment | null;
  pas?: PasPreview | null;
  disclaimer: string;
}

// --- Execution trace types (waterfall / timeline visualization) ---

export interface TraceToolCall {
  tool_name: string;
  server_label: string;
  tool: string;
  status: "pass" | "fail";
  order: number;
  duration_ms: number;
  started_offset_ms: number;
  args_summary: string;
  result_summary: string;
  args_full?: string;    // PHI-redacted raw JSON/text string
  result_full?: string;  // PHI-redacted raw JSON/text string
}

// Per-agent ordered interleave of model calls (kind=llm) and tool calls (kind=tool).
export interface TraceStep {
  kind: "llm" | "tool";
  name: string;
  status: string;
  server_label?: string;
  model?: string;
  duration_ms: number;
  started_offset_ms: number;
  input_tokens?: number;
  output_tokens?: number;
  args_full?: string;
  result_full?: string;
}

export interface TraceAgent {
  name: string;
  status: "done" | "warning" | "error";
  duration_ms: number;
  model: string;
  response_id?: string;  // correlation id for App Insights spans
  session_id?: string;   // Foundry session id for log streaming
  tool_calls: TraceToolCall[];
  steps?: TraceStep[];
}

export interface TracePhase {
  name: PhaseId;
  status: string;
  started_offset_ms: number;
  duration_ms: number;
  agents: TraceAgent[];
}

// Flat, run-wide ordered list backing the Event inspector.
export interface TraceEvent {
  id: number;
  type: "user_input" | "llm_call" | "tool_call" | "final";
  phase: string;
  agent: string;
  label: string;
  status: string;
  duration_ms: number;
  started_offset_ms: number;
  request: string;   // PHI-redacted raw JSON/text string
  response: string;  // PHI-redacted raw JSON/text string
}

export interface ExecutionTrace {
  request_id: string;
  started_at: string;
  completed_at: string;
  total_duration_ms: number;
  phases: TracePhase[];
  events?: TraceEvent[];
}

// --- Observability endpoint types ---

// SSE frame from GET /api/observability/logs/{agent_name}/{session_id}
export interface LogFrame {
  stream: "stdout" | "stderr" | "status";
  message: string;
  timestamp: string;
  // Preamble frame may carry session/agent metadata instead of a log line.
  session_state?: string;
  agent?: string;
  version?: string;
}

// One span from GET /api/observability/traces/{correlation_id}
export interface RunSpan {
  timestamp: string;
  name: string;
  operation: string;
  gen_model: string;
  tool: string;
  agent: string;
  in_tok: number;
  out_tok: number;
  duration: number;
  success: boolean;
  operation_Id: string;
  id: string;
}

export interface RunSpansResponse {
  available: boolean;
  reason: string;
  spans: RunSpan[];
}

// GET /api/observability/links/{correlation_id}
export interface ObsLinks {
  app_insights?: string;
  foundry_traces?: string;
  foundry_project?: string;
  correlation_id?: string;
}

export interface ReviewResponse {
  request_id: string;
  recommendation: "ready_to_submit" | "needs_review" | "approve" | "pend_for_review";
  confidence: number;
  confidence_level: string; // "HIGH" | "MEDIUM" | "LOW"
  summary: string;
  tool_results: ToolResult[];
  clinical_rationale: string;
  coverage_criteria_met: string[];
  coverage_criteria_not_met: string[];
  missing_documentation: string[];
  documentation_gaps: DocumentationGap[];
  policy_references: string[];
  decision_gate?: string;   // "gate_1_provider" | "gate_2_codes" | "gate_3_necessity" | "approved"
  criteria_summary?: string; // e.g. "8 of 8 requirements MET"
  synthesis_audit_trail?: SynthesisAuditTrail;
  disclaimer: string;
  agent_results?: AgentResults;
  audit_trail?: AuditTrail;
  audit_justification?: string;
  audit_justification_pdf?: string;
  execution_trace?: ExecutionTrace | null;
  // CMS-0057 / Da Vinci standards-aligned view (CRD/DTR/PAS). Optional and
  // additive — null when the standards layer is disabled or no pack matches.
  standards?: StandardsAssessment | null;
}

// --- Progress tracking types (SSE streaming) ---

export type PhaseId =
  | "preflight"
  | "phase_1"
  | "phase_2"
  | "phase_3"
  | "phase_4";

export type AgentId =
  | "compliance"
  | "clinical"
  | "coverage"
  | "synthesis";

export type AgentStatus = "pending" | "running" | "done" | "error";

export interface AgentProgress {
  status: AgentStatus;
  detail: string;
}

export interface ProgressEvent {
  phase: PhaseId;
  status: "running" | "completed";
  progress_pct: number;
  message: string;
  agents: Partial<Record<AgentId, AgentProgress>>;
}

export interface ReviewProgress {
  currentPhase: PhaseId;
  progressPct: number;
  message: string;
  agents: Record<AgentId, AgentProgress>;
  phases: Record<PhaseId, "pending" | "running" | "completed">;
  error?: string;
}

// --- Decision & Notification types ---

export interface DecisionRequest {
  request_id: string;
  action: "submit" | "revise" | "accept" | "override";
  override_recommendation?: "ready_to_submit" | "needs_review" | "approve" | "pend_for_review";
  override_rationale?: string;
  reviewer_name: string;
  reviewer_id?: string;
}

export interface NotificationLetter {
  authorization_number: string;
  letter_type: "submission_ready" | "needs_documentation" | "approval" | "pend";
  effective_date: string;
  expiration_date?: string;
  patient_name: string;
  provider_name: string;
  body_text: string;
  appeal_rights?: string;
  documentation_deadline?: string;
  pdf_base64?: string;
}

export interface DecisionResponse {
  request_id: string;
  authorization_number: string;
  final_recommendation: "ready_to_submit" | "needs_review" | "approve" | "pend_for_review";
  decided_by: string;
  decided_at: string;
  was_overridden: boolean;
  override_rationale?: string;
  original_recommendation?: string;
  letter: NotificationLetter;
  updated_audit_justification_pdf?: string;
}
