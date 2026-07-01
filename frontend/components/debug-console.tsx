"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Timer,
  Cpu,
  Layers,
  CheckCircle2,
  AlertCircle,
  XCircle,
  Wrench,
  Sparkles,
  ChevronLeft,
  ChevronRight,
  User,
  Flag,
  Network,
  ListTree,
  Terminal,
  BarChart3,
  ExternalLink,
  Play,
  Square,
  Loader2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { streamSessionLogs, fetchRunSpans, fetchObsLinks } from "@/lib/api";
import type {
  ExecutionTrace as ExecutionTraceType,
  TracePhase,
  TraceAgent,
  TraceToolCall,
  TraceStep,
  TraceEvent,
  LogFrame,
  RunSpan,
  ObsLinks,
  PhaseId,
} from "@/lib/types";

interface DebugConsoleProps {
  trace: ExecutionTraceType | null;
}

const PHASE_LABELS: Record<string, string> = {
  preflight: "Pre-flight",
  phase_1: "Documentation + Clinical (parallel)",
  phase_2: "Policy Matching",
  standards: "Standards Alignment (CRD/DTR/PAS)",
  phase_3: "Submission Readiness",
  phase_4: "Audit Trail",
};

function phaseLabel(name: string): string {
  return PHASE_LABELS[name as PhaseId] ?? name;
}

function formatMs(ms: number): string {
  if (ms == null || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

// Pretty-print a JSON string when parseable; otherwise return the raw text.
function prettyJson(raw: string | undefined | null): string {
  if (raw == null || raw === "") return "";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

// Mirrors the status-badge pattern from progress-tracker.tsx / execution-trace.tsx
function AgentStatusBadge({ status }: { status: string }) {
  if (status === "done") return <Badge variant="success">Done</Badge>;
  if (status === "warning") return <Badge variant="warning">Warning</Badge>;
  if (status === "error") return <Badge variant="destructive">Error</Badge>;
  if (status === "pass") return <Badge variant="success">Pass</Badge>;
  if (status === "fail") return <Badge variant="destructive">Fail</Badge>;
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

// ---------------------------------------------------------------------------
// TIMELINE sub-tab — phase → agent → tool waterfall, enhanced to render `steps`
// (llm model-call bars distinct from tool bars). Click-to-reveal detail kept.
// ---------------------------------------------------------------------------

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
      <Wrench className="mr-1 h-3 w-3 shrink-0 opacity-80" />
      <span className="truncate">{label}</span>
    </button>
  );
}

function ToolCallDetail({ call }: { call: TraceToolCall }) {
  const [showRaw, setShowRaw] = useState(false);
  const hasRaw = !!(call.args_full || call.result_full);
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
      {hasRaw && (
        <div className="pt-0.5">
          <button
            type="button"
            onClick={() => setShowRaw((v) => !v)}
            className="text-[11px] font-medium text-brand hover:underline"
          >
            {showRaw ? "Hide raw payload" : "Show raw payload"}
          </button>
          {showRaw && (
            <div className="mt-1.5 space-y-2">
              {call.args_full && (
                <div>
                  <p className="mb-0.5 font-medium text-muted-foreground">args_full</p>
                  <pre className="max-h-60 overflow-auto rounded bg-background p-2 font-mono text-[11px] leading-relaxed">
                    {prettyJson(call.args_full)}
                  </pre>
                </div>
              )}
              {call.result_full && (
                <div>
                  <p className="mb-0.5 font-medium text-muted-foreground">result_full</p>
                  <pre className="max-h-60 overflow-auto rounded bg-background p-2 font-mono text-[11px] leading-relaxed">
                    {prettyJson(call.result_full)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Distinct bar for an LLM model-call step (vs the tool-call bars).
function StepBar({
  step,
  maxDuration,
  isActive,
  onToggle,
}: {
  step: TraceStep;
  maxDuration: number;
  isActive: boolean;
  onToggle: () => void;
}) {
  const widthPct = Math.max(
    MIN_BAR_PCT,
    maxDuration > 0 ? (step.duration_ms / maxDuration) * 100 : MIN_BAR_PCT
  );
  const isLlm = step.kind === "llm";
  const tokens =
    step.input_tokens != null || step.output_tokens != null
      ? ` · ${step.input_tokens ?? 0}→${step.output_tokens ?? 0} tok`
      : "";
  const failed = step.status === "fail" || step.status === "error";

  // LLM bars use the brand/indigo palette; tool bars reuse success/destructive.
  const palette = isLlm
    ? "bg-brand/85 hover:bg-brand"
    : failed
      ? "bg-destructive/85 hover:bg-destructive"
      : "bg-success/85 hover:bg-success";

  return (
    <button
      type="button"
      onClick={onToggle}
      style={{ width: `${widthPct}%` }}
      title={`${step.name} — ${formatMs(step.duration_ms)}${tokens}`}
      className={`group relative flex h-6 min-w-[2.5rem] items-center overflow-hidden rounded px-2 text-left text-[11px] font-medium text-white transition-all ${palette} ${
        isActive ? "ring-2 ring-offset-1 ring-foreground/40" : ""
      }`}
    >
      {isLlm ? (
        <Sparkles className="mr-1 h-3 w-3 shrink-0 opacity-90" />
      ) : (
        <Wrench className="mr-1 h-3 w-3 shrink-0 opacity-80" />
      )}
      <span className="truncate">{step.name}</span>
    </button>
  );
}

function StepDetail({ step }: { step: TraceStep }) {
  const [showRaw, setShowRaw] = useState(false);
  const hasRaw = !!(step.args_full || step.result_full);
  return (
    <div className="mt-1.5 rounded-md border bg-muted/40 p-2.5 text-xs space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        {step.kind === "llm" ? (
          <Badge className="text-[10px] bg-brand">LLM</Badge>
        ) : (
          <Badge variant="secondary" className="text-[10px]">Tool</Badge>
        )}
        <AgentStatusBadge status={step.status} />
        <span className="font-mono text-muted-foreground">
          {step.model || step.server_label || step.name}
        </span>
        <span className="ml-auto flex items-center gap-2 text-muted-foreground">
          {(step.input_tokens != null || step.output_tokens != null) && (
            <span className="font-mono">
              {step.input_tokens ?? 0}→{step.output_tokens ?? 0} tok
            </span>
          )}
          <span className="flex items-center gap-1">
            <Timer className="h-3 w-3" />
            {formatMs(step.duration_ms)}
          </span>
        </span>
      </div>
      {hasRaw && (
        <div className="pt-0.5">
          <button
            type="button"
            onClick={() => setShowRaw((v) => !v)}
            className="text-[11px] font-medium text-brand hover:underline"
          >
            {showRaw ? "Hide raw payload" : "Show raw payload"}
          </button>
          {showRaw && (
            <div className="mt-1.5 space-y-2">
              {step.args_full && (
                <div>
                  <p className="mb-0.5 font-medium text-muted-foreground">args_full</p>
                  <pre className="max-h-60 overflow-auto rounded bg-background p-2 font-mono text-[11px] leading-relaxed">
                    {prettyJson(step.args_full)}
                  </pre>
                </div>
              )}
              {step.result_full && (
                <div>
                  <p className="mb-0.5 font-medium text-muted-foreground">result_full</p>
                  <pre className="max-h-60 overflow-auto rounded bg-background p-2 font-mono text-[11px] leading-relaxed">
                    {prettyJson(step.result_full)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AgentRow({ agent }: { agent: TraceAgent }) {
  // Two independent selections: one for the steps lane, one for the tool lane.
  const [activeStep, setActiveStep] = useState<number | null>(null);
  const [activeTool, setActiveTool] = useState<number | null>(null);

  const steps = agent.steps ?? [];
  const toolCalls = [...(agent.tool_calls ?? [])].sort(
    (a, b) => a.order - b.order
  );
  const maxStepDuration = steps.reduce(
    (max, s) => Math.max(max, s.duration_ms || 0),
    0
  );
  const maxToolDuration = toolCalls.reduce(
    (max, c) => Math.max(max, c.duration_ms || 0),
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

      {/* Steps lane — ordered llm/tool interleave (when present) */}
      {steps.length > 0 && (
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            <ListTree className="h-3 w-3" />
            Steps
            <span className="flex items-center gap-1 normal-case">
              <Sparkles className="h-3 w-3 text-brand" /> model
              <Wrench className="ml-1.5 h-3 w-3 text-success" /> tool
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-1">
            {steps.map((step, i) => (
              <StepBar
                key={`step-${i}`}
                step={step}
                maxDuration={maxStepDuration}
                isActive={activeStep === i}
                onToggle={() => setActiveStep(activeStep === i ? null : i)}
              />
            ))}
          </div>
          {activeStep !== null && steps[activeStep] && (
            <StepDetail step={steps[activeStep]} />
          )}
        </div>
      )}

      {/* Tool lane — kept from the original waterfall */}
      {toolCalls.length > 0 ? (
        <div className="space-y-1">
          {steps.length > 0 && (
            <div className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              <Wrench className="h-3 w-3" />
              Tool calls
            </div>
          )}
          <div className="flex flex-wrap items-center gap-1">
            {toolCalls.map((call, i) => (
              <ToolCallBar
                key={`${call.order}-${i}`}
                call={call}
                maxDuration={maxToolDuration}
                isActive={activeTool === i}
                onToggle={() => setActiveTool(activeTool === i ? null : i)}
              />
            ))}
          </div>
          {activeTool !== null && toolCalls[activeTool] && (
            <ToolCallDetail call={toolCalls[activeTool]} />
          )}
        </div>
      ) : (
        steps.length === 0 && (
          <p className="text-xs text-muted-foreground">No tool calls.</p>
        )
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
          agents.map((agent, i) => (
            <AgentRow key={`${agent.name}-${i}`} agent={agent} />
          ))
        ) : (
          <p className="text-xs text-muted-foreground">No agents recorded.</p>
        )}
      </div>
    </div>
  );
}

function TimelineTab({ trace }: { trace: ExecutionTraceType }) {
  return (
    <div className="space-y-5">
      {trace.phases.map((phase, i) => (
        <PhaseGroup key={`${phase.name}-${i}`} phase={phase} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// EVENTS sub-tab — ADK-style inspector. Left list + prev/next; right panel with
// Event / Request / Response sub-tabs.
// ---------------------------------------------------------------------------

function EventTypeIcon({ type }: { type: TraceEvent["type"] }) {
  switch (type) {
    case "user_input":
      return <User className="h-3.5 w-3.5 text-brand shrink-0" />;
    case "llm_call":
      return <Sparkles className="h-3.5 w-3.5 text-brand shrink-0" />;
    case "tool_call":
      return <Wrench className="h-3.5 w-3.5 text-muted-foreground shrink-0" />;
    case "final":
      return <Flag className="h-3.5 w-3.5 text-success shrink-0" />;
    default:
      return <Activity className="h-3.5 w-3.5 text-muted-foreground shrink-0" />;
  }
}

function eventDotClass(status: string): string {
  if (status === "fail" || status === "error") return "bg-destructive";
  if (status === "warning") return "bg-warning";
  if (status === "pass" || status === "done") return "bg-success";
  return "bg-muted-foreground/40";
}

function EventsTab({ events }: { events: TraceEvent[] }) {
  const [idx, setIdx] = useState(0);
  const total = events.length;
  const current = events[idx];

  const goPrev = useCallback(
    () => setIdx((i) => Math.max(0, i - 1)),
    []
  );
  const goNext = useCallback(
    () => setIdx((i) => Math.min(total - 1, i + 1)),
    [total]
  );

  if (total === 0 || !current) {
    return (
      <p className="text-sm text-muted-foreground">
        No events recorded for this run.
      </p>
    );
  }

  return (
    <div className="grid gap-3 md:grid-cols-[260px_1fr]">
      {/* Left: event list */}
      <div className="rounded-lg border bg-card">
        <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
          <span className="text-xs font-semibold">
            Event {idx + 1} of {total}
          </span>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              size="icon-xs"
              variant="outline"
              onClick={goPrev}
              disabled={idx === 0}
              aria-label="Previous event"
            >
              <ChevronLeft />
            </Button>
            <Button
              type="button"
              size="icon-xs"
              variant="outline"
              onClick={goNext}
              disabled={idx === total - 1}
              aria-label="Next event"
            >
              <ChevronRight />
            </Button>
          </div>
        </div>
        <div className="max-h-[28rem] overflow-auto p-1.5">
          {events.map((ev, i) => (
            <button
              key={ev.id ?? i}
              type="button"
              onClick={() => setIdx(i)}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors ${
                i === idx ? "bg-muted ring-1 ring-brand/30" : "hover:bg-muted/60"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${eventDotClass(ev.status)}`}
              />
              <EventTypeIcon type={ev.type} />
              <span className="flex-1 truncate font-medium">{ev.label}</span>
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                {formatMs(ev.duration_ms)}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Right: detail with Event / Request / Response sub-tabs */}
      <div className="rounded-lg border bg-card p-3">
        <Tabs defaultValue="event">
          <TabsList variant="line">
            <TabsTrigger value="event">Event</TabsTrigger>
            <TabsTrigger value="request">Request</TabsTrigger>
            <TabsTrigger value="response">Response</TabsTrigger>
          </TabsList>

          <TabsContent value="event" className="pt-3">
            <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-2 text-xs">
              <dt className="font-medium text-muted-foreground">Label</dt>
              <dd className="font-medium">{current.label}</dd>
              <dt className="font-medium text-muted-foreground">Type</dt>
              <dd className="flex items-center gap-1.5">
                <EventTypeIcon type={current.type} />
                {current.type}
              </dd>
              <dt className="font-medium text-muted-foreground">Agent</dt>
              <dd>{current.agent || "—"}</dd>
              <dt className="font-medium text-muted-foreground">Phase</dt>
              <dd>{phaseLabel(current.phase)}</dd>
              <dt className="font-medium text-muted-foreground">Status</dt>
              <dd>
                <AgentStatusBadge status={current.status} />
              </dd>
              <dt className="font-medium text-muted-foreground">Duration</dt>
              <dd className="font-mono">{formatMs(current.duration_ms)}</dd>
              <dt className="font-medium text-muted-foreground">Started at</dt>
              <dd className="font-mono">+{formatMs(current.started_offset_ms)}</dd>
              <dt className="font-medium text-muted-foreground">Event ID</dt>
              <dd className="font-mono">{current.id}</dd>
            </dl>
          </TabsContent>

          <TabsContent value="request" className="pt-3">
            <pre className="max-h-[26rem] overflow-auto rounded bg-muted/50 p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words">
              {prettyJson(current.request) || "No request payload."}
            </pre>
          </TabsContent>

          <TabsContent value="response" className="pt-3">
            <pre className="max-h-[26rem] overflow-auto rounded bg-muted/50 p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words">
              {prettyJson(current.response) || "No response payload."}
            </pre>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// GRAPH sub-tab — agent → tool node graph (inline CSS boxes + SVG edges).
// ---------------------------------------------------------------------------

interface ToolNode {
  tool: string;
  count: number;
  anyFailed: boolean;
}

interface AgentNode {
  name: string;
  status: string;
  tools: ToolNode[];
}

function buildGraph(trace: ExecutionTraceType): AgentNode[] {
  const nodes: AgentNode[] = [];
  for (const phase of trace.phases ?? []) {
    for (const agent of phase.agents ?? []) {
      const toolMap = new Map<string, ToolNode>();
      for (const call of agent.tool_calls ?? []) {
        const key = call.tool || call.tool_name;
        if (!key) continue;
        const existing = toolMap.get(key);
        if (existing) {
          existing.count += 1;
          existing.anyFailed = existing.anyFailed || call.status === "fail";
        } else {
          toolMap.set(key, {
            tool: key,
            count: 1,
            anyFailed: call.status === "fail",
          });
        }
      }
      nodes.push({
        name: agent.name,
        status: agent.status,
        tools: Array.from(toolMap.values()),
      });
    }
  }
  return nodes;
}

function GraphTab({ trace }: { trace: ExecutionTraceType }) {
  const agents = useMemo(() => buildGraph(trace), [trace]);

  if (agents.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No agents to graph for this run.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        Each agent node links to the distinct tools it called. Green = all calls
        passed, red = at least one failed; the badge shows the call count.
      </p>
      <div className="space-y-5">
        {agents.map((agent, i) => (
          <div key={`${agent.name}-${i}`} className="flex items-stretch gap-3">
            {/* Agent node */}
            <div className="flex w-48 shrink-0 items-center">
              <div
                className={`flex w-full items-center gap-2 rounded-lg border-2 bg-card px-3 py-2 ${
                  agent.status === "error"
                    ? "border-destructive/50"
                    : agent.status === "warning"
                      ? "border-warning/50"
                      : "border-brand/50"
                }`}
              >
                <AgentStatusIcon status={agent.status} />
                <span className="truncate text-sm font-semibold">
                  {agent.name}
                </span>
              </div>
            </div>

            {/* Connector */}
            <div className="flex shrink-0 items-center" aria-hidden>
              <svg width="28" height="20" className="overflow-visible">
                <line
                  x1="0"
                  y1="10"
                  x2="28"
                  y2="10"
                  className="stroke-border"
                  strokeWidth="2"
                />
                <polygon points="28,10 22,6 22,14" className="fill-border" />
              </svg>
            </div>

            {/* Tool nodes */}
            <div className="flex flex-wrap items-center gap-2">
              {agent.tools.length > 0 ? (
                agent.tools.map((t, j) => (
                  <div
                    key={`${t.tool}-${j}`}
                    className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs ${
                      t.anyFailed
                        ? "border-destructive/40 bg-destructive/10 text-destructive"
                        : "border-success/40 bg-success/10 text-success-dark"
                    }`}
                  >
                    <Wrench className="h-3 w-3 shrink-0" />
                    <span className="font-medium">{t.tool}</span>
                    <Badge
                      variant={t.anyFailed ? "destructive" : "success"}
                      className="ml-0.5 h-4 px-1.5 text-[10px]"
                    >
                      ×{t.count}
                    </Badge>
                  </div>
                ))
              ) : (
                <span className="text-xs text-muted-foreground">
                  No tool calls
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// FOUNDRY sub-tab — per-agent Foundry-native panel (logs SSE, spans table,
// deep-links).
// ---------------------------------------------------------------------------

const NOT_AVAILABLE = (
  <p className="text-xs italic text-muted-foreground">
    not available in this environment
  </p>
);

function logLineClass(stream: LogFrame["stream"]): string {
  if (stream === "stderr") return "text-destructive";
  if (stream === "status") return "text-brand";
  return "text-foreground/80";
}

function SessionLogStreamer({
  agentName,
  sessionId,
}: {
  agentName: string;
  sessionId: string;
}) {
  const [lines, setLines] = useState<LogFrame[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const preRef = useRef<HTMLPreElement | null>(null);

  // Cancel any in-flight stream on unmount.
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // Keep the terminal scrolled to the latest line.
  useEffect(() => {
    if (preRef.current) preRef.current.scrollTop = preRef.current.scrollHeight;
  }, [lines]);

  const start = useCallback(() => {
    setError(null);
    setLines([]);
    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;
    streamSessionLogs(agentName, sessionId, {
      onLog: (frame) => setLines((prev) => [...prev, frame]),
      onError: (e) => setError(e),
      signal: controller.signal,
    }).finally(() => {
      if (abortRef.current === controller) {
        setStreaming(false);
        abortRef.current = null;
      }
    });
  }, [agentName, sessionId]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }, []);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {streaming ? (
          <Button type="button" size="xs" variant="outline" onClick={stop}>
            <Square className="h-3 w-3" />
            Stop
          </Button>
        ) : (
          <Button type="button" size="xs" variant="outline" onClick={start}>
            <Play className="h-3 w-3" />
            Stream session logs
          </Button>
        )}
        {streaming && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            streaming…
          </span>
        )}
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">
          session {sessionId.slice(0, 12)}
        </span>
      </div>
      {error && (
        <div className="flex items-start gap-1.5 rounded border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          {error}
        </div>
      )}
      {(lines.length > 0 || streaming) && (
        <pre
          ref={preRef}
          className="max-h-64 overflow-auto rounded-md bg-foreground/95 p-3 font-mono text-[11px] leading-relaxed text-background"
        >
          {lines.length === 0 ? (
            <span className="text-background/50">Waiting for log output…</span>
          ) : (
            lines.map((l, i) => (
              <div key={i} className={logLineClass(l.stream)}>
                {l.timestamp && (
                  <span className="text-background/40">
                    {l.timestamp}{" "}
                  </span>
                )}
                {l.session_state || l.agent || l.version ? (
                  <span className="text-brand">
                    [{[l.agent, l.version, l.session_state]
                      .filter(Boolean)
                      .join(" · ")}]
                  </span>
                ) : (
                  <>
                    <span className="text-background/40">
                      {l.stream}:{" "}
                    </span>
                    {l.message}
                  </>
                )}
              </div>
            ))
          )}
        </pre>
      )}
    </div>
  );
}

function SpansLoader({ responseId }: { responseId: string }) {
  const [spans, setSpans] = useState<RunSpan[] | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setReason(null);
    try {
      const res = await fetchRunSpans(responseId);
      if (!res.available) {
        setReason(res.reason || "Spans not available.");
        setSpans([]);
      } else {
        setSpans(res.spans ?? []);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load spans");
    } finally {
      setLoading(false);
    }
  }, [responseId]);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="xs"
          variant="outline"
          onClick={load}
          disabled={loading}
        >
          {loading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <BarChart3 className="h-3 w-3" />
          )}
          Load App Insights spans
        </Button>
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">
          {responseId.slice(0, 16)}
        </span>
      </div>
      {error && (
        <div className="flex items-start gap-1.5 rounded border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          {error}
        </div>
      )}
      {reason && (
        <p className="rounded border bg-muted/40 p-2 text-xs italic text-muted-foreground">
          {reason}
        </p>
      )}
      {spans && spans.length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b bg-muted/40 text-left">
                <th className="px-2 py-1.5 font-medium">Operation</th>
                <th className="px-2 py-1.5 font-medium">Model / Tool</th>
                <th className="px-2 py-1.5 font-medium text-right">Duration</th>
                <th className="px-2 py-1.5 font-medium text-right">Tokens</th>
                <th className="px-2 py-1.5 font-medium">Success</th>
              </tr>
            </thead>
            <tbody>
              {spans.map((s, i) => (
                <tr key={s.id ?? i} className="border-b last:border-0">
                  <td className="px-2 py-1.5">
                    <span className="font-medium">{s.operation || s.name}</span>
                  </td>
                  <td className="px-2 py-1.5 font-mono text-[11px] text-muted-foreground">
                    {s.gen_model || s.tool || "—"}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono">
                    {s.duration != null ? formatMs(s.duration) : "—"}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-muted-foreground">
                    {(s.in_tok ?? 0)}→{(s.out_tok ?? 0)}
                  </td>
                  <td className="px-2 py-1.5">
                    {s.success ? (
                      <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                    ) : (
                      <XCircle className="h-3.5 w-3.5 text-destructive" />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function FoundryAgentPanel({ agent }: { agent: TraceAgent }) {
  const hasSession = !!agent.session_id;
  const hasResponse = !!agent.response_id;

  return (
    <div className="rounded-lg border bg-card p-3 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <AgentStatusIcon status={agent.status} />
        <span className="text-sm font-medium">{agent.name}</span>
        {agent.model && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Cpu className="h-3 w-3" />
            {agent.model}
          </span>
        )}
        <AgentStatusBadge status={agent.status} />
      </div>

      {/* Session logs */}
      <div>
        <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <Terminal className="h-3.5 w-3.5" />
          Session logs
        </p>
        {hasSession ? (
          <SessionLogStreamer
            agentName={agent.name}
            sessionId={agent.session_id as string}
          />
        ) : (
          NOT_AVAILABLE
        )}
      </div>

      {/* App Insights spans */}
      <div>
        <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <BarChart3 className="h-3.5 w-3.5" />
          App Insights spans
        </p>
        {hasResponse ? (
          <SpansLoader responseId={agent.response_id as string} />
        ) : (
          NOT_AVAILABLE
        )}
      </div>
    </div>
  );
}

function ObsLinkButtons({ requestId }: { requestId: string }) {
  const [links, setLinks] = useState<ObsLinks | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchObsLinks(requestId)
      .then((res) => {
        if (active) setLinks(res);
      })
      .catch(() => {
        if (active) setLinks({});
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [requestId]);

  if (loading) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading deep links…
      </span>
    );
  }

  const entries: { label: string; href?: string }[] = [
    { label: "App Insights", href: links?.app_insights },
    { label: "Foundry Traces", href: links?.foundry_traces },
    { label: "Foundry Project", href: links?.foundry_project },
  ].filter((e) => !!e.href);

  if (entries.length === 0) return NOT_AVAILABLE;

  return (
    <div className="flex flex-wrap gap-2">
      {entries.map((e) => (
        <Button
          key={e.label}
          asChild
          size="xs"
          variant="outline"
        >
          <a href={e.href} target="_blank" rel="noopener noreferrer">
            <ExternalLink className="h-3 w-3" />
            {e.label}
          </a>
        </Button>
      ))}
    </div>
  );
}

function FoundryTab({ trace }: { trace: ExecutionTraceType }) {
  const agents = useMemo(() => {
    const out: TraceAgent[] = [];
    for (const phase of trace.phases ?? []) {
      for (const agent of phase.agents ?? []) out.push(agent);
    }
    return out;
  }, [trace]);

  const foundryAgents = agents.filter((a) => a.session_id || a.response_id);

  return (
    <div className="space-y-4">
      {/* Deep links keyed off the run-wide request id */}
      <div className="rounded-lg border bg-muted/30 p-3">
        <p className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <ExternalLink className="h-3.5 w-3.5" />
          Observability deep links
        </p>
        {trace.request_id ? (
          <ObsLinkButtons requestId={trace.request_id} />
        ) : (
          NOT_AVAILABLE
        )}
      </div>

      {foundryAgents.length > 0 ? (
        foundryAgents.map((agent, i) => (
          <FoundryAgentPanel key={`${agent.name}-${i}`} agent={agent} />
        ))
      ) : (
        <p className="text-sm text-muted-foreground">
          No agents expose a Foundry session or response id in this environment.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Debug Console container
// ---------------------------------------------------------------------------

export function DebugConsole({ trace }: DebugConsoleProps) {
  if (!trace || !trace.phases || trace.phases.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed py-12 text-center">
        <Activity className="h-8 w-8 text-muted-foreground/50" />
        <p className="text-sm font-medium text-muted-foreground">No trace yet</p>
        <p className="text-xs text-muted-foreground/70 max-w-sm">
          The debug console populates once an assessment runs. Phases stream in
          as each completes.
        </p>
      </div>
    );
  }

  const events = trace.events ?? [];

  return (
    <Card className="shadow-sm">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-lg flex items-center gap-2">
            <Activity className="h-5 w-5 text-primary" />
            Debug Console
          </CardTitle>
          <div className="flex items-center gap-2">
            {trace.request_id && (
              <Badge variant="outline" className="font-mono text-[11px]">
                {trace.request_id.slice(0, 12)}
              </Badge>
            )}
            <Badge variant="outline" className="font-mono text-sm">
              <Timer className="mr-1 h-3.5 w-3.5" />
              Total {formatMs(trace.total_duration_ms)}
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="timeline">
          <TabsList>
            <TabsTrigger value="timeline" className="flex items-center gap-1.5">
              <Layers className="h-3.5 w-3.5" />
              Timeline
            </TabsTrigger>
            <TabsTrigger value="events" className="flex items-center gap-1.5">
              <ListTree className="h-3.5 w-3.5" />
              Events
            </TabsTrigger>
            <TabsTrigger value="graph" className="flex items-center gap-1.5">
              <Network className="h-3.5 w-3.5" />
              Graph
            </TabsTrigger>
            <TabsTrigger value="foundry" className="flex items-center gap-1.5">
              <Terminal className="h-3.5 w-3.5" />
              Foundry
            </TabsTrigger>
          </TabsList>

          <TabsContent value="timeline" className="pt-4">
            <TimelineTab trace={trace} />
          </TabsContent>
          <TabsContent value="events" className="pt-4">
            <EventsTab events={events} />
          </TabsContent>
          <TabsContent value="graph" className="pt-4">
            <GraphTab trace={trace} />
          </TabsContent>
          <TabsContent value="foundry" className="pt-4">
            <FoundryTab trace={trace} />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
