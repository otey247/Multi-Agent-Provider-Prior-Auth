"use client";

import { useState, useCallback } from "react";
import type { ReviewResponse, DecisionResponse, ExecutionTrace as ExecutionTraceType } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { ConfidenceBar } from "@/components/confidence-bar";
import { AgentDetails } from "@/components/agent-details";
import { ExecutionTrace } from "@/components/execution-trace";
import { DecisionPanel } from "@/components/decision-panel";
import {
  CheckCircle2,
  ShieldAlert,
  Scale,
  AlertTriangle,
  FileWarning,
  BookOpen,
  ScrollText,
  ClipboardList,
  TrendingUp,
  Database,
  Clock,
  Info,
  Download,
  FileText,
  Activity,
} from "lucide-react";

interface ReviewDashboardProps {
  review: ReviewResponse;
  liveTrace?: ExecutionTraceType | null;
}

export function ReviewDashboard({ review: rawReview, liveTrace }: ReviewDashboardProps) {
  // Normalize potentially-null arrays from API response
  const review = {
    ...rawReview,
    tool_results: rawReview.tool_results ?? [],
    coverage_criteria_met: rawReview.coverage_criteria_met ?? [],
    coverage_criteria_not_met: rawReview.coverage_criteria_not_met ?? [],
    missing_documentation: rawReview.missing_documentation ?? [],
    documentation_gaps: rawReview.documentation_gaps ?? [],
    policy_references: rawReview.policy_references ?? [],
    audit_trail: rawReview.audit_trail ? {
      ...rawReview.audit_trail,
      data_sources: rawReview.audit_trail.data_sources ?? [],
    } : undefined,
  };

  // Track the audit PDF — updated when a decision (especially an override) returns a new one
  const [auditPdf, setAuditPdf] = useState<string | undefined>(review.audit_justification_pdf);

  const handleDecision = useCallback((decision: DecisionResponse) => {
    if (decision.updated_audit_justification_pdf) {
      setAuditPdf(decision.updated_audit_justification_pdf);
    }
  }, []);

  function handleDownloadJustification() {
    if (auditPdf) {
      const byteChars = atob(auditPdf);
      const byteNumbers = new Uint8Array(byteChars.length);
      for (let i = 0; i < byteChars.length; i++) {
        byteNumbers[i] = byteChars.charCodeAt(i);
      }
      const blob = new Blob([byteNumbers], { type: "application/pdf" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit-justification-${review.request_id.slice(0, 8)}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } else if (review.audit_justification) {
      const blob = new Blob([review.audit_justification], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit-justification-${review.request_id.slice(0, 8)}.md`;
      a.click();
      URL.revokeObjectURL(url);
    }
  }

  // Prefer the full trace carried on the final ReviewResponse (replay);
  // fall back to the latest live snapshot streamed during the run.
  const trace = review.execution_trace ?? liveTrace ?? null;

  return (
    <div className="mt-8 animate-in fade-in-0 slide-in-from-bottom-4 duration-500">
      <Tabs defaultValue="assessment" className="space-y-6">
        <TabsList>
          <TabsTrigger value="assessment" className="flex items-center gap-1.5">
            <ClipboardList className="h-3.5 w-3.5" />
            Assessment
          </TabsTrigger>
          <TabsTrigger value="trace" className="flex items-center gap-1.5">
            <Activity className="h-3.5 w-3.5" />
            Execution Trace
          </TabsTrigger>
        </TabsList>

        <TabsContent value="assessment" className="space-y-6">
      {/* Submission readiness header */}
      <Card className="shadow-sm border-l-4 border-l-primary">
        <CardHeader>
          <div className="flex flex-wrap items-center gap-3">
            <CardTitle className="text-lg flex items-center gap-2">
              <ClipboardList className="h-5 w-5 text-primary" />
              Submission Readiness Assessment
            </CardTitle>
            <Badge
              variant={
                review.recommendation === "ready_to_submit" || review.recommendation === "approve"
                  ? "success"
                  : "warning"
              }
              className="text-sm px-3 py-1"
            >
              {review.recommendation === "ready_to_submit" || review.recommendation === "approve"
                ? "Ready to Submit"
                : "Needs Review"}
            </Badge>
            <Badge variant="outline" className="text-sm">{review.confidence_level}</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <p className="text-sm font-medium mb-1">Confidence</p>
            <ConfidenceBar value={review.confidence <= 1 ? Math.round(review.confidence * 100) : Math.round(review.confidence)} className="max-w-sm" />
          </div>

          <div>
            <p className="text-sm font-medium mb-1">Summary</p>
            <p className="text-sm text-muted-foreground">{review.summary}</p>
          </div>
        </CardContent>
      </Card>

      {/* Tool checks */}
      {(review.tool_results?.length ?? 0) > 0 && (
        <Card className="shadow-sm">
          <CardHeader>
            <CardTitle className="text-sm font-medium uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
              <CheckCircle2 className="h-4 w-4" />
              Verification Checks
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col gap-2">
              {(review.tool_results ?? []).map((t, i) => (
                <Badge
                  key={i}
                  className="whitespace-normal text-left justify-start max-w-full"
                  variant={
                    t.status === "pass"
                      ? "success"
                      : t.status === "warning"
                        ? "warning"
                        : "destructive"
                  }
                >
                  {t.tool_name}: {t.detail}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Payer requirements */}
      {((review.coverage_criteria_met?.length ?? 0) > 0 ||
        (review.coverage_criteria_not_met?.length ?? 0) > 0) && (
        <Card className="shadow-sm">
          <CardHeader>
            <CardTitle className="text-sm font-medium uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
              <Scale className="h-4 w-4" />
              Payer Policy Requirements
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {(review.coverage_criteria_met?.length ?? 0) > 0 && (
              <div>
                <p className="text-sm font-medium text-success-dark mb-1 flex items-center gap-1.5">
                  <CheckCircle2 className="h-4 w-4" />
                  Requirements Met
                </p>
                <ul className="list-disc list-inside text-sm text-muted-foreground space-y-0.5">
                  {(review.coverage_criteria_met ?? []).map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </div>
            )}
            {(review.coverage_criteria_not_met?.length ?? 0) > 0 && (
              <div>
                <p className="text-sm font-medium text-destructive mb-1 flex items-center gap-1.5">
                  <ShieldAlert className="h-4 w-4" />
                  Requirements Not Yet Met
                </p>
                <ul className="list-disc list-inside text-sm text-muted-foreground space-y-0.5">
                  {(review.coverage_criteria_not_met ?? []).map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Missing documentation */}
      {(review.missing_documentation?.length ?? 0) > 0 && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Documentation Action Required</AlertTitle>
          <AlertDescription>
            <ul className="list-disc list-inside mt-1 space-y-0.5">
              {(review.missing_documentation ?? []).map((doc, i) => (
                <li key={i}>{doc}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      {/* Documentation gaps */}
      {(review.documentation_gaps?.length ?? 0) > 0 && (
        <Card className="shadow-sm">
          <CardHeader>
            <CardTitle className="text-sm font-medium uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
              <FileWarning className="h-4 w-4" />
              Documentation Gaps
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {(review.documentation_gaps ?? []).map((gap, i) => (
              <div key={i} className="flex items-start gap-2 text-sm">
                <Badge
                  variant={gap.critical ? "destructive" : "warning"}
                  className="mt-0.5 shrink-0"
                >
                  {gap.critical ? "Required" : "Recommended"}
                </Badge>
                <div>
                  <p className="font-medium">{gap.what}</p>
                  <p className="text-muted-foreground">{gap.request}</p>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Policy references */}
      {(review.policy_references?.length ?? 0) > 0 && (
        <Card className="shadow-sm">
          <CardHeader>
            <CardTitle className="text-sm font-medium uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
              <BookOpen className="h-4 w-4" />
              Payer Policy References
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="list-disc list-inside text-sm text-muted-foreground space-y-0.5">
              {(review.policy_references ?? []).map((ref, i) => (
                <li key={i}>{ref}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* Clinical evidence rationale */}
      {review.clinical_rationale && (
        <Card className="shadow-sm">
          <CardHeader>
            <CardTitle className="text-sm font-medium uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
              <ScrollText className="h-4 w-4" />
              Clinical Evidence Rationale
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground whitespace-pre-wrap">
              {review.clinical_rationale}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Agent details */}
      {review.agent_results && (
        <AgentDetails results={review.agent_results} synthesis={review} />
      )}

      {/* Decision panel */}
      <DecisionPanel review={review} onDecision={handleDecision} />

      {/* Audit trail */}
      {review.audit_trail && (
        <Card className="shadow-sm bg-muted/30">
          <CardHeader>
            <CardTitle className="text-sm font-medium uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
              <Database className="h-4 w-4" />
              Assessment Audit Trail
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
              <div>
                <p className="font-medium flex items-center gap-1.5"><Clock className="h-3.5 w-3.5 text-muted-foreground" />Assessment Started</p>
                <p className="text-muted-foreground ml-5">
                  {review.audit_trail.review_started}
                </p>
              </div>
              <div>
                <p className="font-medium flex items-center gap-1.5"><Clock className="h-3.5 w-3.5 text-muted-foreground" />Assessment Completed</p>
                <p className="text-muted-foreground ml-5">
                  {review.audit_trail.review_completed}
                </p>
              </div>
              <div>
                <p className="font-medium flex items-center gap-1.5"><CheckCircle2 className="h-3.5 w-3.5 text-muted-foreground" />Requirements Met</p>
                <p className="text-muted-foreground ml-5">
                  {review.audit_trail.criteria_met_count}
                </p>
              </div>
              <div>
                <p className="font-medium flex items-center gap-1.5"><TrendingUp className="h-3.5 w-3.5 text-muted-foreground" />Evidence Extraction Confidence</p>
                <ConfidenceBar
                  value={review.audit_trail.extraction_confidence}
                  className="w-32 ml-5"
                />
              </div>
              <div>
                <p className="font-medium flex items-center gap-1.5"><TrendingUp className="h-3.5 w-3.5 text-muted-foreground" />Policy Matching Confidence</p>
                <ConfidenceBar
                  value={review.audit_trail.assessment_confidence}
                  className="w-32 ml-5"
                />
              </div>
              {(review.audit_trail.data_sources?.length ?? 0) > 0 && (
                <div className="col-span-full">
                  <p className="font-medium mb-1 flex items-center gap-1.5"><Database className="h-3.5 w-3.5 text-muted-foreground" />Data Sources Consulted</p>
                  <div className="flex flex-wrap gap-1 ml-5">
                    {(review.audit_trail.data_sources ?? []).map((src, i) => (
                      <Badge key={i} variant="outline" className="text-xs">
                        {src}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Download submission readiness report */}
      {(auditPdf || review.audit_justification) && (
        <Card className="shadow-sm border border-info/30 bg-gradient-to-r from-info-light/60 to-info-light/30">
          <CardContent className="py-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-info-light">
                  <FileText className="h-5 w-5 text-info" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-foreground">Submission Readiness Report</p>
                  <p className="text-xs text-muted-foreground">
                    Full 8-section report with requirement evaluations, validation checks, evidence review, and readiness rationale
                  </p>
                </div>
              </div>
              <Button
                onClick={handleDownloadJustification}
                variant="outline"
                size="sm"
                className="border-info/50 text-info hover:bg-info-light hover:text-info"
              >
                <Download className="mr-1.5 h-4 w-4" />
                Download Report
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Disclaimer */}
      {review.disclaimer && (
        <>
          <Separator />
          <div className="flex items-start gap-2 px-1">
            <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted-foreground" />
            <p className="text-xs text-muted-foreground italic">{review.disclaimer}</p>
          </div>
        </>
      )}
        </TabsContent>

        <TabsContent value="trace">
          <ExecutionTrace trace={trace} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
