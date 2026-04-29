"use client";

import { useState, useMemo } from "react";
import { toast } from "sonner";
import { Check, ArrowRightLeft, Download, Loader2, Gavel, Award, FileText, CheckCircle2, AlertTriangle } from "lucide-react";
import { submitDecision } from "@/lib/api";
import type { ReviewResponse, DecisionResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface Props {
  review: ReviewResponse;
  onDecision?: (decision: DecisionResponse) => void;
}

type Mode = "initial" | "override" | "submitted";

/** Normalize legacy recommendation values to provider-side display labels. */
function normalizeRec(rec: string): string {
  if (rec === "approve" || rec === "ready_to_submit") return "ready_to_submit";
  return "needs_review";
}

export function DecisionPanel({ review, onDecision }: Props) {
  const [mode, setMode] = useState<Mode>("initial");
  const [reviewerName, setReviewerName] = useState("");
  const normalizedRec = normalizeRec(review.recommendation);
  const [overrideRec, setOverrideRec] = useState<"ready_to_submit" | "needs_review">(
    normalizedRec === "ready_to_submit" ? "needs_review" : "ready_to_submit"
  );
  const [overrideRationale, setOverrideRationale] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decision, setDecision] = useState<DecisionResponse | null>(null);

  const isReadyToSubmit =
    decision?.final_recommendation === "ready_to_submit" ||
    decision?.final_recommendation === "approve";

  // Build a blob URL for the PDF viewer when we have PDF data
  const pdfBlobUrl = useMemo(() => {
    if (!decision?.letter.pdf_base64) return null;
    try {
      const byteChars = atob(decision.letter.pdf_base64);
      const byteNumbers = new Uint8Array(byteChars.length);
      for (let i = 0; i < byteChars.length; i++) {
        byteNumbers[i] = byteChars.charCodeAt(i);
      }
      const blob = new Blob([byteNumbers], { type: "application/pdf" });
      return URL.createObjectURL(blob);
    } catch {
      return null;
    }
  }, [decision?.letter.pdf_base64]);

  const handleAccept = async () => {
    if (!reviewerName.trim()) {
      setError("Reviewer name is required");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await submitDecision({
        request_id: review.request_id,
        action: "submit",
        reviewer_name: reviewerName.trim(),
      });
      setDecision(resp);
      setMode("submitted");
      onDecision?.(resp);
      toast.success("Authorization recorded", {
        description: `Ref #${resp.authorization_number}`,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Decision failed");
      toast.error("Decision failed");
    } finally {
      setLoading(false);
    }
  };

  const handleOverrideSubmit = async () => {
    if (!reviewerName.trim()) {
      setError("Reviewer name is required");
      return;
    }
    if (!overrideRationale.trim()) {
      setError("Rationale is required when revising the assessment");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await submitDecision({
        request_id: review.request_id,
        action: "revise",
        override_recommendation: overrideRec,
        override_rationale: overrideRationale.trim(),
        reviewer_name: reviewerName.trim(),
      });
      setDecision(resp);
      setMode("submitted");
      onDecision?.(resp);
      toast.success("Revised assessment recorded", {
        description: `Ref #${resp.authorization_number}`,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Decision failed");
      toast.error("Revision failed");
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = () => {
    if (!decision) return;
    if (decision.letter.pdf_base64) {
      const byteChars = atob(decision.letter.pdf_base64);
      const byteNumbers = new Uint8Array(byteChars.length);
      for (let i = 0; i < byteChars.length; i++) {
        byteNumbers[i] = byteChars.charCodeAt(i);
      }
      const blob = new Blob([byteNumbers], { type: "application/pdf" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${decision.authorization_number}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } else {
      const blob = new Blob([decision.letter.body_text], {
        type: "text/plain",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${decision.authorization_number}.txt`;
      a.click();
      URL.revokeObjectURL(url);
    }
  };

  if (mode === "submitted" && decision) {
    return (
      <Card className="mt-6 bg-muted/30 shadow-sm">
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Award className="h-5 w-5 text-success" />
            Authorization Recorded
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <Badge variant="success" className="text-sm px-3 py-1.5">
              Ref #: {decision.authorization_number}
            </Badge>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold">Submission Status:</span>
            <Badge
              variant={isReadyToSubmit ? "success" : "warning"}
              className="text-sm px-3 py-1.5 font-bold"
            >
              {isReadyToSubmit ? (
                <CheckCircle2 className="mr-1.5 h-4 w-4" />
              ) : (
                <AlertTriangle className="mr-1.5 h-4 w-4" />
              )}
              {isReadyToSubmit ? "READY TO SUBMIT" : "NEEDS REVIEW"}
            </Badge>
            {decision.was_overridden && (
              <Badge variant="outline" className="text-warning border-warning/50">
                Staff Revised
              </Badge>
            )}
          </div>
          {decision.was_overridden && decision.override_rationale && (
            <div className="rounded-md border border-warning/30 bg-warning/5 p-3 space-y-1">
              <p className="text-sm font-semibold text-warning">Staff Override</p>
              {decision.original_recommendation && (
                <p className="text-xs text-muted-foreground">
                  Original AI Assessment:{" "}
                  <span className="font-medium">
                    {normalizeRec(decision.original_recommendation) === "ready_to_submit"
                      ? "READY TO SUBMIT"
                      : "NEEDS REVIEW"}
                  </span>
                </p>
              )}
              <p className="text-sm">{decision.override_rationale}</p>
            </div>
          )}
          <div>
            <p className="text-sm font-medium mb-2 flex items-center gap-1.5">
              <FileText className="h-3.5 w-3.5 text-muted-foreground" />
              Provider Letter
            </p>
            {pdfBlobUrl ? (
              <div className="rounded-md border overflow-hidden">
                <iframe
                  src={pdfBlobUrl}
                  className="w-full h-[500px]"
                  title="Provider Letter PDF"
                />
              </div>
            ) : (
              <ScrollArea className="h-[300px] rounded-md border bg-card p-4">
                <pre className="whitespace-pre-wrap font-mono text-xs">
                  {decision.letter.body_text}
                </pre>
              </ScrollArea>
            )}
          </div>
          <div className="flex items-center gap-3">
            <Button onClick={handleDownload} className="bg-gradient-to-r from-brand to-brand-dark hover:from-brand-hover hover:to-brand-hover-dark text-white shadow-sm">
              <Download className="mr-2 h-4 w-4" />
              Download Provider Letter
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Reviewed by: {decision.decided_by} | {decision.decided_at}
          </p>
        </CardContent>
      </Card>
    );
  }

  if (mode === "override") {
    return (
      <Card className="mt-6 bg-muted/30 shadow-sm">
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <ArrowRightLeft className="h-5 w-5 text-warning" />
            Revise Assessment
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="reviewer_override">Staff Reviewer Name</Label>
            <Input
              id="reviewer_override"
              value={reviewerName}
              onChange={(e) => setReviewerName(e.target.value)}
              placeholder="Jane Doe, Prior Auth Specialist"
            />
          </div>
          <div className="space-y-2">
            <Label>Revised Assessment</Label>
            <Select
              value={overrideRec}
              onValueChange={(v) =>
                setOverrideRec(v as "ready_to_submit" | "needs_review")
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ready_to_submit">Ready to Submit</SelectItem>
                <SelectItem value="needs_review">Needs Review</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="rationale">Rationale (required)</Label>
            <Textarea
              id="rationale"
              value={overrideRationale}
              onChange={(e) => setOverrideRationale(e.target.value)}
              placeholder="Explain why you are revising the AI assessment..."
              className="min-h-[80px]"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={handleOverrideSubmit}
              disabled={loading}
              className="bg-warning text-white hover:bg-warning-dark"
            >
              {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {loading ? "Submitting..." : "Submit Revision"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setMode("initial");
                setError(null);
              }}
              disabled={loading}
            >
              Cancel
            </Button>
          </div>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="mt-6 bg-muted/30 shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Gavel className="h-5 w-5 text-primary" />
          Staff Review Decision
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="reviewer_name">Staff Reviewer Name</Label>
          <Input
            id="reviewer_name"
            value={reviewerName}
            onChange={(e) => setReviewerName(e.target.value)}
            placeholder="Jane Doe, Prior Auth Specialist"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={handleAccept}
            disabled={loading}
            className="bg-success text-white hover:bg-success-dark"
          >
            {loading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Check className="mr-2 h-4 w-4" />
            )}
            {loading ? "Submitting..." : "Accept AI Assessment"}
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              setMode("override");
              setError(null);
            }}
            disabled={loading}
            className="border border-warning/50 bg-warning-light text-warning-dark hover:bg-warning/20"
          >
            <ArrowRightLeft className="mr-2 h-4 w-4" />
            Revise Assessment
          </Button>
        </div>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}
