"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Building2,
  CalendarDays,
  ClipboardList,
  CreditCard,
  FileText,
  FlaskConical,
  Hash,
  Hospital,
  Loader2,
  Plus,
  Send,
  Stethoscope,
  User,
  X,
} from "lucide-react";
import type { PriorAuthRequest, ReviewResponse, ReviewProgress, ProgressEvent, AgentId } from "@/lib/types";
import { submitReviewStream } from "@/lib/api";
import { DEFAULT_SAMPLE_CASE_ID, SAMPLE_CASES } from "@/lib/sample-case";
import { ProgressTracker } from "@/components/progress-tracker";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface UploadFormProps {
  onReviewComplete: (review: ReviewResponse) => void;
}

const emptyRequest: PriorAuthRequest = {
  patient_name: "",
  patient_dob: "",
  provider_npi: "",
  diagnosis_codes: [""],
  procedure_codes: [""],
  clinical_notes: "",
  insurance_id: "",
  ordering_provider_name: "",
  ordering_provider_npi: "",
  rendering_provider_specialty: "",
  servicing_facility: "",
  payer_name: "",
  payer_plan: "",
  urgency: "standard",
  place_of_service: "",
  attached_note_types: [],
  prior_treatment_history: [],
};

export function UploadForm({ onReviewComplete }: UploadFormProps) {
  const [form, setForm] = useState<PriorAuthRequest>(emptyRequest);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<ReviewProgress | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [selectedSampleId, setSelectedSampleId] = useState(DEFAULT_SAMPLE_CASE_ID);

  const selectedSample = useMemo(
    () => SAMPLE_CASES.find((sample) => sample.id === selectedSampleId) ?? SAMPLE_CASES[0],
    [selectedSampleId]
  );

  const initialProgress: ReviewProgress = {
    currentPhase: "preflight",
    progressPct: 0,
    message: "Starting review...",
    agents: {
      compliance: { status: "pending", detail: "Waiting" },
      clinical: { status: "pending", detail: "Waiting" },
      coverage: { status: "pending", detail: "Waiting" },
      synthesis: { status: "pending", detail: "Waiting" },
    },
    phases: {
      preflight: "pending",
      phase_1: "pending",
      phase_2: "pending",
      phase_3: "pending",
      phase_4: "pending",
    },
  };

  function applyProgressEvent(prev: ReviewProgress, event: ProgressEvent): ReviewProgress {
    const next = { ...prev };
    next.currentPhase = event.phase;
    next.progressPct = event.progress_pct;
    next.message = event.message;
    next.phases = { ...prev.phases, [event.phase]: event.status };
    next.agents = { ...prev.agents };
    for (const [agentId, agentState] of Object.entries(event.agents ?? {})) {
      next.agents[agentId as AgentId] = agentState;
    }
    return next;
  }

  function updateField<K extends keyof PriorAuthRequest>(
    key: K,
    value: PriorAuthRequest[K]
  ) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function updateCode(
    field: "diagnosis_codes" | "procedure_codes",
    index: number,
    value: string
  ) {
    const updated = [...form[field]];
    updated[index] = value;
    updateField(field, updated);
  }

  function updateListField(
    field: "attached_note_types" | "prior_treatment_history",
    value: string
  ) {
    updateField(
      field,
      value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean)
    );
  }

  function addCode(field: "diagnosis_codes" | "procedure_codes") {
    updateField(field, [...form[field], ""]);
  }

  function removeCode(
    field: "diagnosis_codes" | "procedure_codes",
    index: number
  ) {
    if (form[field].length <= 1) return;
    updateField(
      field,
      form[field].filter((_, i) => i !== index)
    );
  }

  function loadSelectedSample() {
    setForm({ ...selectedSample.request });
    setShowAdvanced(true);
    setError(null);
    toast.success("Provider sample loaded", {
      description: selectedSample.title,
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setProgress(initialProgress);

    const cleaned: PriorAuthRequest = {
      ...form,
      diagnosis_codes: form.diagnosis_codes.map((c) => c.trim()).filter(Boolean),
      procedure_codes: form.procedure_codes.map((c) => c.trim()).filter(Boolean),
      attached_note_types: (form.attached_note_types ?? []).map((c) => c.trim()).filter(Boolean),
      prior_treatment_history: (form.prior_treatment_history ?? []).map((c) => c.trim()).filter(Boolean),
    };

    submitReviewStream(
      cleaned,
      (event) => {
        setProgress((prev) => prev ? applyProgressEvent(prev, event) : prev);
      },
      (result) => {
        setLoading(false);
        setProgress(null);
        onReviewComplete(result);
        const rec = result.recommendation;
        const isReady = rec === "ready_to_submit" || rec === "approve";
        toast.success("Assessment complete", {
          description: isReady
            ? "Status: Ready to Submit"
            : "Status: Needs Review",
        });
      },
      (errMsg) => {
        setLoading(false);
        setProgress((prev) => prev ? { ...prev, error: errMsg } : prev);
        setError(errMsg);
        toast.error("Review failed", { description: errMsg });
      },
    );
  }

  return (
    <Card className="shadow-sm">
      <CardHeader className="flex flex-col gap-4 pb-2 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <CardTitle className="text-lg flex items-center gap-2">
            <ClipboardList className="h-5 w-5 text-primary" />
            New Provider Prior Auth Intake
          </CardTitle>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            Capture the case as a PA coordinator, utilization review nurse, or revenue-cycle specialist would assemble it before payer submission.
          </p>
        </div>
        <div className="w-full max-w-md space-y-2">
          <Label className="text-xs uppercase tracking-wide text-muted-foreground">
            Provider sample cases
          </Label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Select value={selectedSampleId} onValueChange={setSelectedSampleId}>
              <SelectTrigger className="sm:flex-1">
                <SelectValue placeholder="Choose a sample case" />
              </SelectTrigger>
              <SelectContent>
                {SAMPLE_CASES.map((sample) => (
                  <SelectItem key={sample.id} value={sample.id}>
                    {sample.specialty} · {sample.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="secondary" size="sm" onClick={loadSelectedSample} type="button">
              <FlaskConical className="mr-1 h-3.5 w-3.5" />
              Load Sample
            </Button>
          </div>
        </div>
      </CardHeader>

      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-6">
          <div className="rounded-lg border bg-muted/30 p-4 space-y-3">
            <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="font-medium text-sm">{selectedSample.title}</p>
                <p className="text-sm text-muted-foreground">
                  {selectedSample.specialty} · {selectedSample.summary}
                </p>
              </div>
              <Button type="button" variant="outline" size="sm" onClick={() => setShowAdvanced((prev) => !prev)}>
                {showAdvanced ? "Use Simple Intake" : "Show EHR/FHIR-Style Intake"}
              </Button>
            </div>
            <p className="text-sm">{selectedSample.scenario}</p>
            <div className="grid gap-4 lg:grid-cols-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Likely documentation gaps</p>
                <ul className="mt-1 list-disc pl-4 text-sm space-y-1">
                  {selectedSample.documentationGaps.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Expected agent findings</p>
                <ul className="mt-1 list-disc pl-4 text-sm space-y-1">
                  {selectedSample.expectedFindings.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Likely staff next actions</p>
                <ul className="mt-1 list-disc pl-4 text-sm space-y-1">
                  {selectedSample.nextActions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="patient_name" className="flex items-center gap-1.5">
                <User className="h-3.5 w-3.5 text-muted-foreground" />
                Patient Name
              </Label>
              <Input
                id="patient_name"
                placeholder="Jane Doe"
                value={form.patient_name}
                onChange={(e) => updateField("patient_name", e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="patient_dob" className="flex items-center gap-1.5">
                <CalendarDays className="h-3.5 w-3.5 text-muted-foreground" />
                Date of Birth
              </Label>
              <Input
                id="patient_dob"
                type="date"
                value={form.patient_dob}
                onChange={(e) => updateField("patient_dob", e.target.value)}
                required
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="provider_npi" className="flex items-center gap-1.5">
                <Hash className="h-3.5 w-3.5 text-muted-foreground" />
                Billing / Submitting Provider NPI
              </Label>
              <Input
                id="provider_npi"
                placeholder="1234567890"
                value={form.provider_npi}
                onChange={(e) => updateField("provider_npi", e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="insurance_id" className="flex items-center gap-1.5">
                <CreditCard className="h-3.5 w-3.5 text-muted-foreground" />
                Member / Insurance ID (optional)
              </Label>
              <Input
                id="insurance_id"
                placeholder="MCR-123456789A"
                value={form.insurance_id ?? ""}
                onChange={(e) => updateField("insurance_id", e.target.value)}
              />
            </div>
          </div>

          {showAdvanced && (
            <div className="space-y-4 rounded-lg border p-4">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Hospital className="h-4 w-4 text-primary" />
                Advanced provider intake (EHR / FHIR-style mapping)
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                <div className="space-y-2">
                  <Label htmlFor="ordering_provider_name">Ordering Provider Name</Label>
                  <Input
                    id="ordering_provider_name"
                    placeholder="Ordering clinician"
                    value={form.ordering_provider_name ?? ""}
                    onChange={(e) => updateField("ordering_provider_name", e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="ordering_provider_npi">Ordering Provider NPI</Label>
                  <Input
                    id="ordering_provider_npi"
                    placeholder="1234567890"
                    value={form.ordering_provider_npi ?? ""}
                    onChange={(e) => updateField("ordering_provider_npi", e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="rendering_provider_specialty">Rendering Specialty</Label>
                  <Input
                    id="rendering_provider_specialty"
                    placeholder="Pulmonology, Oncology, Orthopedics..."
                    value={form.rendering_provider_specialty ?? ""}
                    onChange={(e) => updateField("rendering_provider_specialty", e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="servicing_facility">Servicing Facility / Department</Label>
                  <Input
                    id="servicing_facility"
                    placeholder="Infusion center, ASC, hospital department"
                    value={form.servicing_facility ?? ""}
                    onChange={(e) => updateField("servicing_facility", e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="payer_name">Payer</Label>
                  <Input
                    id="payer_name"
                    placeholder="Traditional Medicare, BCBS, UHC..."
                    value={form.payer_name ?? ""}
                    onChange={(e) => updateField("payer_name", e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="payer_plan">Plan / Product</Label>
                  <Input
                    id="payer_plan"
                    placeholder="Part B, PPO, MA HMO..."
                    value={form.payer_plan ?? ""}
                    onChange={(e) => updateField("payer_plan", e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="urgency">Urgency</Label>
                  <Select
                    value={form.urgency ?? "standard"}
                    onValueChange={(value: "standard" | "urgent") => updateField("urgency", value)}
                  >
                    <SelectTrigger id="urgency">
                      <SelectValue placeholder="Select urgency" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="standard">Standard</SelectItem>
                      <SelectItem value="urgent">Urgent / Expedited</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="place_of_service">Place of Service</Label>
                  <Input
                    id="place_of_service"
                    placeholder="Office, ASC, outpatient, home..."
                    value={form.place_of_service ?? ""}
                    onChange={(e) => updateField("place_of_service", e.target.value)}
                  />
                </div>
              </div>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="attached_note_types" className="flex items-center gap-1.5">
                    <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                    Attached Note Types / Documents
                  </Label>
                  <Input
                    id="attached_note_types"
                    placeholder="Progress note, MRI report, pathology report"
                    value={(form.attached_note_types ?? []).join(", ")}
                    onChange={(e) => updateListField("attached_note_types", e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">Comma-separated document names from the chart, fax packet, or referral queue.</p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="prior_treatment_history" className="flex items-center gap-1.5">
                    <Building2 className="h-3.5 w-3.5 text-muted-foreground" />
                    Prior Treatment History
                  </Label>
                  <Input
                    id="prior_treatment_history"
                    placeholder="PT, injections, chemo regimen, home oxygen trial"
                    value={(form.prior_treatment_history ?? []).join(", ")}
                    onChange={(e) => updateListField("prior_treatment_history", e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">Use this to mirror discrete treatment-history fields from an EHR, UM queue, or referral system.</p>
                </div>
              </div>
            </div>
          )}

          <div className="relative">
            <div className="absolute inset-0 flex items-center"><span className="w-full border-t" /></div>
            <div className="relative flex justify-center text-xs uppercase"><span className="bg-card px-2 text-muted-foreground">Codes</span></div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label className="flex items-center gap-1.5">
                <Stethoscope className="h-3.5 w-3.5 text-muted-foreground" />
                Diagnosis Codes (ICD-10)
              </Label>
              {form.diagnosis_codes.map((code, i) => (
                <div key={i} className="flex gap-1">
                  <Input
                    placeholder="e.g. R91.1"
                    value={code}
                    onChange={(e) =>
                      updateCode("diagnosis_codes", i, e.target.value)
                    }
                  />
                  {form.diagnosis_codes.length > 1 && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => removeCode("diagnosis_codes", i)}
                    >
                      <X className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => addCode("diagnosis_codes")}
              >
                <Plus className="mr-1 h-3.5 w-3.5" />
                Add Diagnosis Code
              </Button>
            </div>

            <div className="space-y-2">
              <Label className="flex items-center gap-1.5">
                <Hash className="h-3.5 w-3.5 text-muted-foreground" />
                Procedure / HCPCS Codes
              </Label>
              {form.procedure_codes.map((code, i) => (
                <div key={i} className="flex gap-1">
                  <Input
                    placeholder="e.g. 31628 or J9303"
                    value={code}
                    onChange={(e) =>
                      updateCode("procedure_codes", i, e.target.value)
                    }
                  />
                  {form.procedure_codes.length > 1 && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => removeCode("procedure_codes", i)}
                    >
                      <X className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => addCode("procedure_codes")}
              >
                <Plus className="mr-1 h-3.5 w-3.5" />
                Add Procedure Code
              </Button>
            </div>
          </div>

          <div className="relative">
            <div className="absolute inset-0 flex items-center"><span className="w-full border-t" /></div>
            <div className="relative flex justify-center text-xs uppercase"><span className="bg-card px-2 text-muted-foreground">Clinical Documentation</span></div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="clinical_notes" className="flex items-center gap-1.5">
              <FileText className="h-3.5 w-3.5 text-muted-foreground" />
              Clinical Notes / Intake Narrative
            </Label>
            <Textarea
              id="clinical_notes"
              rows={6}
              placeholder="Enter the provider-side summary that would accompany the prior auth packet: diagnosis history, failed treatments, diagnostics, severity, and requested service rationale."
              value={form.clinical_notes}
              onChange={(e) => updateField("clinical_notes", e.target.value)}
              required
            />
          </div>

          {progress && (
            <ProgressTracker progress={progress} />
          )}

          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <Button type="submit" className="w-full bg-gradient-to-r from-brand to-brand-dark hover:from-brand-hover hover:to-brand-hover-dark text-white shadow-md" disabled={loading}>
            {loading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Send className="mr-2 h-4 w-4" />
            )}
            {loading ? "Running Provider Intake Assessment..." : "Assess Prior Auth Packet"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
