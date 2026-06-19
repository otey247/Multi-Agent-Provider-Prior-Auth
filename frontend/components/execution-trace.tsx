"use client";

import { useState } from "react";
import {
  Activity,
  Timer,
  Cpu,
  Layers,
  CheckCircle2,
  AlertCircle,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  ExecutionTrace as ExecutionTraceType,
  TracePhase,
  TraceAgent,
  TraceToolCall,
  PhaseId,
} from "@/lib/types";

interface ExecutionTraceProps {
  trace: ExecutionTraceType | null;
}

const PHASE_LABELS: Record<PhaseId, string> = {
  preflight: "Pre-flight",
  phase_1: "Documentation + Clinical (parallel)",
  phase_2: "Policy Matching",
  phase_3: "Submission Readiness",
  phase_4: "Audit Trail",
};

function phaseLabel(name: string): string {
  return PHASE_LABELS[name as PhaseId] ?? name;
}

function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

// Mirrors the status-badge pattern from progress-tracker.tsx / agent-details.tsx
function AgentStatusBadge({ status }: { status: string }) {
  if (status === "done") return <Badge variant="success">Done</Badge>;
  if (status === "warning") return <Badge variant="warning">Warning</Badge>;
  if (status === "error") return <Badge variant="destructive">Error</Badge>;
  return <Badge variant="secondary">{status}</Badge>;
}

function AgentStatusIcon({ status }: { status: string }) {
  if (status === "done")
    return <CheckCircle2 className="h-4 w-4 text-success shrink-0" />;
  if (status === "warning")
    return <AlertCircle className="h-4 w-4 text-warning shrink-0" />;
  if (status === "error")
    return <XCircle className="h-4 w-4 text-destructive shrink-0" />;
  return <Activity className="h-4 w-4 text-muted-foreground shrink-0" />;
}

const MIN_BAR_PCT = 4; // ensure tiny tool calls remain visible

function ToolCallBar({
  call,
  maxDuration,
  isActive,
  onToggle,
}: {
  call: TraceToolCall;
  maxDuration: number;
  isActive: boolean;
  onToggle: () => void;
}) {
  const widthPct = Math.max(
    MIN_BAR_PCT,
    maxDuration > 0 ? (call.duration_ms / maxDuration) * 100 : MIN_BAR_PCT
  );
  const isPass = call.status === "pass";
  const label = call.tool || call.tool_name;

  return (
    <button
      type="button"
      onClick={onToggle}
      style={{ width: `${widthPct}%` }}
      title={`${label} — ${formatMs(call.duration_ms)}`}
      className={`group relative flex h-6 min-w-[2.5rem] items-center overflow-hidden rounded px-2 text-left text-[11px] font-medium text-white transition-all ${
        isPass
          ? "bg-success/85 hover:bg-success"
          : "bg-destructive/85 hover:bg-destructive"
      } ${isActive ? "ring-2 ring-offset-1 ring-brand" : ""}`}
    >
      <span className="truncate">{label}</span>
    </button>
  );
}

function ToolCallDetail({ call }: { call: TraceToolCall }) {
  return (
    <div className="mt-1.5 rounded-md border bg-muted/40 p-2.5 text-xs space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant={call.status === "pass" ? "success" : "destructive"}
          className="text-[10px]"
        >
          {call.status}
        </Badge>
        <span className="font-mono text-muted-foreground">
          {call.server_label}
          {call.tool ? ` · ${call.tool}` : ""}
        </span>
        <span className="ml-auto flex items-center gap-1 text-muted-foreground">
          <Timer className="h-3 w-3" />
          {formatMs(call.duration_ms)}
        </span>
      </div>
      {call.args_summary && (
        <div>
          <span className="font-medium">Args: </span>
          <span className="text-muted-foreground break-words">
            {call.args_summary}
          </span>
        </div>
      )}
      {call.result_summary && (
        <div>
          <span className="font-medium">Result: </span>
          <span className="text-muted-foreground break-words">
            {call.result_summary}
          </span>
        </div>
      )}
    </div>
  );
}

function AgentRow({ agent }: { agent: TraceAgent }) {
  const [activeIdx, setActiveIdx] = useState<number | null>(null);
  const toolCalls = [...(agent.tool_calls ?? [])].sort(
    (a, b) => a.order - b.order
  );
  const maxDuration = toolCalls.reduce(
    (max, c) => Math.max(max, c.duration_ms),
    0
  );

  return (
    <div className="rounded-lg border bg-card p-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <AgentStatusIcon status={agent.status} />
        <span className="text-sm font-medium">{agent.name}</span>
        {agent.model && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Cpu className="h-3 w-3" />
            {agent.model}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <span className="flex items-center gap-1 font-mono text-xs text-muted-foreground">
            <Timer className="h-3 w-3" />
            {formatMs(agent.duration_ms)}
          </span>
          <AgentStatusBadge status={agent.status} />
        </div>
      </div>

      {toolCalls.length > 0 ? (
        <>
          <div className="flex flex-wrap items-center gap-1">
            {toolCalls.map((call, i) => (
              <ToolCallBar
                key={`${call.order}-${i}`}
                call={call}
                maxDuration={maxDuration}
                isActive={activeIdx === i}
                onToggle={() => setActiveIdx(activeIdx === i ? null : i)}
              />
            ))}
          </div>
          {activeIdx !== null && toolCalls[activeIdx] && (
            <ToolCallDetail call={toolCalls[activeIdx]} />
          )}
        </>
      ) : (
        <p className="text-xs text-muted-foreground">No tool calls.</p>
      )}
    </div>
  );
}

function PhaseGroup({ phase }: { phase: TracePhase }) {
  const agents = phase.agents ?? [];
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Layers className="h-4 w-4 text-brand shrink-0" />
        <span className="text-sm font-semibold">{phaseLabel(phase.name)}</span>
        <span className="flex items-center gap-1 font-mono text-xs text-muted-foreground">
          <Timer className="h-3 w-3" />
          {formatMs(phase.duration_ms)}
        </span>
        {phase.status && (
          <Badge variant="outline" className="text-[10px]">
            {phase.status}
          </Badge>
        )}
      </div>
      <div className="ml-1.5 space-y-2 border-l-2 border-brand/20 pl-3">
        {agents.length > 0 ? (
          agents.map((agent, i) => <AgentRow key={`${agent.name}-${i}`} agent={agent} />)
        ) : (
          <p className="text-xs text-muted-foreground">No agents recorded.</p>
        )}
      </div>
    </div>
  );
}

export function ExecutionTrace({ trace }: ExecutionTraceProps) {
  if (!trace || !trace.phases || trace.phases.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed py-12 text-center">
        <Activity className="h-8 w-8 text-muted-foreground/50" />
        <p className="text-sm font-medium text-muted-foreground">No trace yet</p>
        <p className="text-xs text-muted-foreground/70 max-w-sm">
          Execution trace will appear here once an assessment runs. Phases stream
          in as each completes.
        </p>
      </div>
    );
  }

  return (
    <Card className="shadow-sm">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-lg flex items-center gap-2">
            <Activity className="h-5 w-5 text-primary" />
            Execution Trace
          </CardTitle>
          <Badge variant="outline" className="font-mono text-sm">
            <Timer className="mr-1 h-3.5 w-3.5" />
            Total {formatMs(trace.total_duration_ms)}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {trace.phases.map((phase, i) => (
          <PhaseGroup key={`${phase.name}-${i}`} phase={phase} />
        ))}
      </CardContent>
    </Card>
  );
}
