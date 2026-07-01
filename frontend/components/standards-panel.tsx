"use client";

import { useState } from "react";
import type { StandardsAssessment, RequirementEvaluation } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Shield,
  GitBranch,
  ListChecks,
  FileCheck,
  CheckCircle2,
  AlertCircle,
  XCircle,
  ChevronDown,
  ChevronRight,
  Info,
} from "lucide-react";

const STATUS_VARIANT: Record<string, "success" | "warning" | "destructive" | "secondary"> = {
  MET: "success",
  INSUFFICIENT: "warning",
  MISSING: "destructive",
  NOT_APPLICABLE: "secondary",
};

function StatusIcon({ status }: { status: string }) {
  if (status === "MET") return <CheckCircle2 className="h-4 w-4 text-success" />;
  if (status === "INSUFFICIENT") return <AlertCircle className="h-4 w-4 text-warning" />;
  if (status === "MISSING") return <XCircle className="h-4 w-4 text-destructive" />;
  return <Info className="h-4 w-4 text-muted-foreground" />;
}

function RequirementRow({ e }: { e: RequirementEvaluation }) {
  return (
    <div className="flex items-start gap-2 py-2 border-b border-border/50 last:border-0">
      <span className="mt-0.5 shrink-0">
        <StatusIcon status={e.status} />
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={STATUS_VARIANT[e.status] ?? "secondary"} className="text-xs">
            {e.status}
          </Badge>
          {e.conditional && (
            <Badge variant="outline" className="text-xs">conditional</Badge>
          )}
          {!e.required && !e.conditional && (
            <Badge variant="outline" className="text-xs">optional</Badge>
          )}
          <span className="text-xs text-muted-foreground">{e.confidence}%</span>
        </div>
        <p className="text-sm mt-1">{e.description}</p>
        {e.evidence.length > 0 && (
          <p className="text-xs text-muted-foreground mt-0.5">
            <span className="font-medium">Evidence:</span> {e.evidence.join("; ")}
          </p>
        )}
        {e.status !== "MET" && e.gap_action && (
          <p className="text-xs text-warning-dark mt-0.5">
            <span className="font-medium">Action:</span> {e.gap_action}
          </p>
        )}
      </div>
    </div>
  );
}

export function StandardsPanel({ standards }: { standards: StandardsAssessment }) {
  const [open, setOpen] = useState(true);

  if (!standards?.enabled) return null;

  // No payer-specific pack matched — show a compact informational note.
  if (!standards.policy_pack_matched) {
    const reason =
      standards.crd?.reasons?.[0] ??
      "No payer-specific policy pack matched; runtime Medicare LCD/NCD search applies.";
    return (
      <Card className="shadow-sm border-l-4 border-l-muted">
        <CardHeader>
          <CardTitle className="text-sm font-medium uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
            <Shield className="h-4 w-4" />
            CMS-0057 / Da Vinci Alignment
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">{reason}</p>
        </CardContent>
      </Card>
    );
  }

  const crd = standards.crd;
  const dtr = standards.dtr;
  const pas = standards.pas;

  const paBadge =
    crd?.pa_required === true ? (
      <Badge variant="warning" className="text-sm">Prior authorization required</Badge>
    ) : crd?.pa_required === false ? (
      <Badge variant="success" className="text-sm">No prior auth required</Badge>
    ) : (
      <Badge variant="secondary" className="text-sm">PA requirement unknown</Badge>
    );

  return (
    <Card className="shadow-sm border-l-4 border-l-info">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-lg flex items-center gap-2">
            <Shield className="h-5 w-5 text-info" />
            CMS-0057 / Da Vinci Alignment
          </CardTitle>
          <Badge variant="outline" className="text-xs">Standards-aware</Badge>
        </div>
        <p className="text-sm text-muted-foreground mt-1">
          {standards.payer} {standards.plan} · {standards.policy_name}
          {standards.policy_version ? ` (v${standards.policy_version})` : ""}
        </p>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* CRD — Coverage Requirements Discovery */}
        {crd && (
          <div>
            <p className="text-sm font-medium mb-2 flex items-center gap-1.5">
              <GitBranch className="h-4 w-4 text-info" />
              Coverage Requirements Discovery (CRD)
            </p>
            <div className="flex flex-wrap items-center gap-2">
              {paBadge}
              {crd.routing_channel && (
                <Badge variant="outline" className="text-sm">
                  Route: {crd.routing_channel}
                </Badge>
              )}
            </div>
          </div>
        )}

        {/* DTR — Documentation Templates & Rules */}
        {dtr && (
          <div>
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="w-full flex items-center justify-between gap-2 text-left"
            >
              <span className="text-sm font-medium flex items-center gap-1.5">
                <ListChecks className="h-4 w-4 text-info" />
                Documentation Requirements (DTR)
              </span>
              <span className="flex items-center gap-2">
                <Badge
                  variant={
                    dtr.requirements_met === dtr.requirements_total ? "success" : "warning"
                  }
                  className="text-xs"
                >
                  {dtr.requirements_met}/{dtr.requirements_total} met
                </Badge>
                {open ? (
                  <ChevronDown className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                )}
              </span>
            </button>
            {open && (
              <div className="mt-2">
                {dtr.requirement_evaluations.map((e) => (
                  <RequirementRow key={e.requirement_id} e={e} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* PAS — Prior Authorization Support package preview */}
        {pas && (
          <div>
            <p className="text-sm font-medium mb-2 flex items-center gap-1.5">
              <FileCheck className="h-4 w-4 text-info" />
              Prior Auth Submission (PAS) — package preview
            </p>
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <Badge variant={pas.pas_ready ? "success" : "warning"} className="text-sm">
                {pas.pas_ready ? "PAS package ready" : "Not yet PAS-ready"}
              </Badge>
              {pas.submission_channel && (
                <Badge variant="outline" className="text-sm">
                  Channel: {pas.submission_channel}
                </Badge>
              )}
            </div>

            {Object.keys(pas.package_summary ?? {}).length > 0 && (
              <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3 mb-2">
                {Object.entries(pas.package_summary).map(([k, v]) => (
                  <div key={k}>
                    <p className="text-xs font-medium capitalize text-muted-foreground">
                      {k.replace(/_/g, " ")}
                    </p>
                    <p className="truncate" title={String(v)}>{String(v) || "—"}</p>
                  </div>
                ))}
              </div>
            )}

            {pas.missing_for_submission.length > 0 && (
              <div>
                <p className="text-xs font-medium text-warning-dark mb-1">
                  Before submission:
                </p>
                <ul className="list-disc list-inside text-sm text-muted-foreground space-y-0.5">
                  {pas.missing_for_submission.map((m, i) => (
                    <li key={i}>{m}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {standards.disclaimer && (
          <p className="text-xs text-muted-foreground italic flex items-start gap-1.5">
            <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            {standards.disclaimer}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
