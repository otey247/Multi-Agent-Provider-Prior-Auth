# Technical Notes

## Architecture Overview

The backend is a **pure HTTP dispatcher** (FastAPI). It has no local AI runtime.
All specialist reasoning runs in four independent Foundry Hosted Agent containers.

```
Frontend (Next.js / ACA)
  └── POST /api/review/stream   (SSE)
        └── FastAPI Backend / Orchestrator (ACA)
              │
              ├─── [Docker Compose — local dev] ──────────────────────────────────────
              │    POST {HOSTED_AGENT_*_URL}/responses
              │    → Clinical / Compliance / Coverage / Synthesis Container
              │
              └─── [Foundry Hosted Agents — production (azd up)] ──────────────────
                   POST {AZURE_AI_PROJECT_ENDPOINT}
                          /agents/{name}/endpoint/protocols/openai/responses
                          ?api-version=v1
                   with  Authorization: Bearer <DefaultAzureCredential>
                         Foundry-Features: HostedAgents=V1Preview
                   → Foundry Agent Service → registered agent containers
```

Each agent container is a **Foundry Hosted Agent** built on the
`azure-ai-agentserver` **Responses protocol host**
(`azure-ai-agentserver-core` + `azure-ai-agentserver-responses`). A
`ResponsesAgentServerHost` exposes the HTTP endpoint and dispatches to a single
`@app.response_handler`. Agents are registered with **Microsoft Foundry** via
`scripts/register_agents.py`. The hosted-agent Responses protocol version is
`1.0.0`; the model is `gpt-5.4`.

The two MCP-backed agents (clinical, coverage) drive the model directly with
the **OpenAI SDK** against `{AZURE_AI_PROJECT_ENDPOINT}/openai/v1`, using the
agent's managed identity (`DefaultAzureCredential`, scope
`https://ai.azure.com/.default`) as the bearer token. Structured output is
enforced with `client.responses.parse(text_format=<PydanticModel>)`. The two
tool-free agents (compliance, synthesis) run on **Microsoft Agent Framework**
(`AzureOpenAIResponsesClient(...).as_agent(...)`) with the same project
endpoint and managed identity, enforcing structure via
`default_options={"response_format": <PydanticModel>}`.

---

## MCP Tool Connections (Foundry Toolbox)

`clinical-reviewer-agent` and `coverage-assessment-agent` consume MCP tools
through a **Foundry Toolbox** — a managed MCP endpoint *on the project domain*
that proxies tool calls out to the backing MCP servers from Foundry's own
network. The agent connects to the toolbox as an MCP **streamable-HTTP client**
(the toolbox endpoint cannot be passed to the Responses API as a `type: mcp`
`server_url`; it must be consumed by an MCP client).

Toolbox endpoint shape (one per agent):

```
{AZURE_AI_PROJECT_ENDPOINT}/toolboxes/{clinical-tools|coverage-tools}/mcp?api-version=v1
```

Every toolbox request carries the managed-identity bearer token plus the header
`Foundry-Features: Toolboxes=V1Preview`.

Per request, the agent's handler (`mcp_toolbox.run_with_toolbox`) opens the MCP
`ClientSession` inside an `AsyncExitStack` — created and closed in the same
handler coroutine, which structurally avoids the cross-task anyio teardown that
a module-level MCP client would hit. It then lists the toolbox tools, maps them
to Responses function tools, and drives a `responses.parse` tool-calling loop to
a final structured result. The loop continues each turn with
`previous_response_id` (server-stored state) rather than re-sending the
conversation — this keeps the reasoning items intact across turns, which gpt-5.4
requires. If the tool budget (`max_iters=8`) is exhausted, the agent forces a
final structured answer with no tools.

Tools are exposed to the model as `{server_label}___{tool_name}`
(e.g. `icd10___validate_code`). The two toolboxes mirror the per-agent split:

| Toolbox          | server_label tools                    |
|------------------|---------------------------------------|
| `clinical-tools` | `icd10`, `pubmed`, `clinical_trials`  |
| `coverage-tools` | `npi`, `cms_coverage`                 |

Backing MCP servers:

- A self-hosted **medical-data** MCP server (Azure Container App; Streamable
  HTTP; stateless; public read-only data) serves `icd10`, `clinical_trials`,
  `npi`, and `cms_coverage`. Its base URL is injected as `MEDICAL_MCP_BASE_URL`.
- **PubMed** (`https://pubmed.mcp.claude.com/mcp`, unauthenticated).

The toolboxes are created/verified by `scripts/create_toolbox.py`; all tools
are registered with `require_approval="never"` because the medical-data calls
are read-only (search/validate/lookup). `scripts/register_agents.py` also
registers the same five MCP servers as Foundry project connections (visible in
the portal under **Build → Tools**) and injects each agent's `TOOLBOX_ENDPOINT`.

`compliance-agent` and `synthesis-agent` use **no tools** — they reason purely
over the request data and the upstream agent outputs.

---

## Agent Skills

The MCP-backed agents (clinical, coverage) read their `skills/<name>/SKILL.md`
from disk at startup and inline the body into the system prompt:

```python
system_prompt = _BASE_INSTRUCTIONS + "\n\n# Skill: clinical-review\n\n" + _load_skill()
```

The tool-free agents (compliance, synthesis) load their skill via
`SkillsProvider`, passed to `.as_agent(context_providers=[skills_provider])`:

```python
skills_provider = SkillsProvider(
    skill_paths=str(Path(__file__).parent / "skills")
)
```

SKILL.md files live alongside the agent:

```
agents/
  clinical/skills/clinical-review/SKILL.md      # ICD-10 validation, clinical extraction (< 60% warning), literature + trials
  coverage/skills/coverage-assessment/SKILL.md  # Provider NPI, specialty-procedure match, CMS policy, criteria mapping
  compliance/skills/compliance-review/SKILL.md  # 10-item checklist (items 9: NCCI, 10: service type are non-blocking)
  synthesis/skills/synthesis-decision/SKILL.md  # Gate rubric, weighted confidence, synthesis_audit_trail output
```

---

## Structured Output

Each agent container declares a local Pydantic model in `schemas.py`.

The MCP-backed agents (clinical, coverage) enforce it with the OpenAI SDK's
`responses.parse(text_format=...)`. The structured JSON is read back from
`output_parsed` (falling back to `output_text`):

```python
# agents/clinical/main.py + mcp_toolbox.py
resp = await client.responses.parse(
    model=deployment,
    instructions=system_prompt,
    input=[{"role": "user", "content": input_text}],
    text_format=ClinicalResult,
    tools=tool_specs,          # toolbox tools, when discovered
)
output_text = resp.output_parsed.model_dump_json()
```

The tool-free agents (compliance, synthesis) enforce it through Microsoft Agent
Framework's `default_options`:

```python
agent = AzureOpenAIResponsesClient(...).as_agent(
    name="synthesis-agent",
    id="synthesis-agent",   # matches the registered agent name
    tools=[],
    context_providers=[skills_provider],
    default_options={"response_format": SynthesisOutput},
)
```

In both cases the schema is a token-level JSON constraint at inference time —
no post-processing or regex extraction is needed. The backend dispatcher reads
the text payload out of the Foundry Responses reply and `json.loads()` it.

The Pydantic models live in each agent container:

| Agent | Schema file | Root model |
|-------|-------------|------------|
| Clinical | `agents/clinical/schemas.py` | `ClinicalResult` |
| Compliance | `agents/compliance/schemas.py` | `ComplianceResult` |
| Coverage | `agents/coverage/schemas.py` | `CoverageResult` |
| Synthesis | `agents/synthesis/schemas.py` | `SynthesisOutput` (includes `synthesis_audit_trail: str` — JSON-encoded audit trail with `gate_results` and `confidence_components`; parsed back to `dict` by the orchestrator) |

---

## Orchestration Flow

```
Phase 1 (parallel):   Compliance + Clinical agents
Phase 2 (sequential): Coverage agent (receives clinical findings)
Phase 3:              Synthesis agent (receives all three results)
Phase 4:              Audit trail + PDF generation
```

### Resilience

| Mechanism | Where | What it does |
|-----------|-------|-------------|
| Result validation | `_validate_agent_result()` | Checks expected top-level keys |
| Automatic retry | `_safe_run()` | Retries once if validation fails |
| SSE status warnings | Phase events | Reports status "warning" for incomplete results |
| Tool result normalization | `_normalize_tool_result()` | Maps non-standard status values |

### Decision Gate (LENIENT MODE)

Gate 1: Provider NPI verification → Gate 2: Code validation → Gate 3: Medical necessity

Default to **PEND** at any uncertain gate. Never DENY in LENIENT mode.

---

## Decision and Notification Flow

1. Review completes → stored in-memory (reviewed via `GET /api/reviews`)
2. Frontend shows Accept / Override panel
3. `POST /api/decision` prevents double-decisions (409)
4. Generates thread-safe authorization number (`PA-YYYYMMDD-XXXXX`)
5. Produces notification letter (approval or pend) in text and PDF

**Letter types:**
- **Approval** — auth number, 90-day validity, coverage criteria met, clinical rationale
- **Pend** — confidence level, missing documentation, 30-day deadline, appeal rights

---

## CPT/HCPCS Validation

Pre-flight step before agents execute:

1. **Format validation** — regex for CPT (5-digit) or HCPCS (letter + 4 digits)
2. **Curated lookup** — ~30 common PA-trigger codes
3. **Results injected** into synthesis prompt for Gate 2

---

## Sample Data

The frontend now includes **multiple provider sample cases** that can be loaded from the intake screen:

| Sample case | Workflow type | Example focus |
|-------------|---------------|---------------|
| Advanced Imaging Follow-up → Bronchoscopic Biopsy | Pulmonology | Imaging progression, biopsy readiness, specialty alignment |
| Specialty Drug / Infusion Start of Care | Oncology | Biomarkers, line-of-therapy, site-of-care documentation |
| Outpatient Surgery Scheduling | Orthopedics | Conservative treatment history, imaging, functional limitation |
| DME / Home Health Oxygen Setup | Pulmonology / Home Care | Face-to-face timing, qualifying tests, supplier packet completeness |

The intake form also supports an **advanced EHR/FHIR-style mode** with ordering-provider, servicing-facility, payer, urgency, attachment-type, and prior-treatment fields.

---

## Observability

All five processes — the FastAPI backend and all four agent containers — export
OpenTelemetry traces and metrics to **Azure Application Insights** via
`azure-monitor-opentelemetry`. Agent traces are also visible in the Foundry
portal's built-in Traces view when App Insights is linked to the Foundry project.

### Process Roles

| Process | `OTEL_SERVICE_NAME` | What it instruments |
|---------|---------------------|---------------------|
| FastAPI backend | `prior-auth-backend` | HTTP requests/responses, outgoing httpx calls to agents, logs, exceptions, live metrics |
| Clinical agent | `agent-clinical` | Responses model calls, MCP toolbox tool calls, token metrics |
| Coverage agent | `agent-coverage` | Same as above |
| Compliance agent | `agent-compliance` | Responses model calls, token metrics |
| Synthesis agent | `agent-synthesis` | Same as above |

Each process configures observability differently based on its role:

- **Backend** (`observability.py`): Calls `configure_azure_monitor()` directly
  before the FastAPI app starts. This is the standard Azure Monitor SDK pattern.
- **Agent containers**: Do NOT call `configure_azure_monitor()` manually.
  Instead, the `azure-ai-agentserver` Responses host wires up OTel tracing
  internally when `ResponsesAgentServerHost().run()` starts, driven entirely by
  the environment variables the agent sets beforehand
  (`APPLICATIONINSIGHTS_CONNECTION_STRING` and `OTEL_SERVICE_NAME`). Agent code
  only sets those env vars; it does not configure exporters itself.

### Content Recording (Sensitive Data)

The Responses host records full LLM prompts, tool arguments, and results in
telemetry spans by default.

> **⚠️ Production consideration:** This stores PA request content (patient names,
> DOBs, diagnoses, clinical notes) in Application Insights telemetry. Ensure your
> App Insights resource has appropriate access controls and data retention
> policies, or reduce App Insights data retention to the minimum required period.

### Application Map

Because `OTEL_SERVICE_NAME` is set in every process, App Insights
**Application Map** renders a clean 5-node topology:

```
prior-auth-backend
  ├──► agent-compliance
  ├──► agent-clinical
  ├──► agent-coverage
  └──► agent-synthesis
```

Edges are drawn from the backend's auto-instrumented outgoing httpx dependency
spans. W3C trace context headers propagate across process boundaries so App
Insights stitches the end-to-end call graph automatically — no manual
correlation ID wiring is needed.

`OTEL_SERVICE_NAME` is set via `os.environ.setdefault(...)` so an explicit
env var configured in the Container App (e.g., via Bicep or the ACA portal)
always overrides the in-code default.

### Trace Hierarchy (backend layer)

```
prior_auth_review (request_id)
  ├── phase_1_parallel
  │     ├── compliance_agent_dispatch
  │     └── clinical_agent_dispatch
  ├── phase_2_coverage
  │     └── coverage_agent_dispatch
  ├── phase_3_synthesis
  │     └── synthesis_agent_dispatch
  └── phase_4_audit
```

### Agent-layer spans

The agent containers emit OTel spans for their Responses model calls and (for
clinical/coverage) their MCP toolbox tool calls, with token metrics. These spans
are children of the backend `*_agent_dispatch` dependency spans, creating an
end-to-end trace from HTTP request → backend orchestration → agent model/tool
calls.

### Custom Backend Span Attributes

| Span | Key attributes |
|------|---------------|
| `prior_auth_review` | `request_id` |
| `phase_1_parallel` | `compliance_status`, `clinical_status` |
| `phase_2_coverage` | `coverage_status` |
| `phase_3_synthesis` | `recommendation`, `confidence` |
| `phase_4_audit` | `confidence`, `confidence_level` |

### Enabling Observability

Set the same connection string in all five containers (Bicep injects this
automatically from the shared `monitoring` module output):

```env
APPLICATION_INSIGHTS_CONNECTION_STRING=InstrumentationKey=<key>;IngestionEndpoint=...
```

**Important: env var names for agent containers.** The `azure-ai-agentserver`
Responses host reads a different connection-string env var than the Azure
Monitor SDK, and the Foundry platform reserves the `APPLICATION*INSIGHTS` names
in the registration payload. `register_agents.py` therefore passes the
connection string to hosted agents as `MONITORING_CONNECTION_STRING`, and each
agent's `main.py` bridges it at startup:

| Package | Env var name | Convention |
|---------|-------------|------------|
| `azure-monitor-opentelemetry` (backend) | `APPLICATION_INSIGHTS_CONNECTION_STRING` | Azure Monitor SDK |
| `azure-ai-agentserver` (agent host) | `APPLICATIONINSIGHTS_CONNECTION_STRING` | Azure App Service |

Agent code bridges this by reading `APPLICATION_INSIGHTS_CONNECTION_STRING` or
`MONITORING_CONNECTION_STRING` and calling
`os.environ.setdefault("APPLICATIONINSIGHTS_CONNECTION_STRING", ...)`. Without
the host-expected name, the Responses host skips tracing setup.

Locally (docker-compose), the variable is intentionally absent — all
observability blocks are no-ops so the app runs without App Insights.

---

## Hosted Agent Dispatch Settings

`hosted_agents.py` automatically selects the dispatch mode based on environment:

- **URL set** (`HOSTED_AGENT_*_URL`): direct HTTP `POST {url}/responses` to the container — Docker Compose mode
- **URL empty + `AZURE_AI_PROJECT_ENDPOINT` set**: `POST {project_endpoint}/agents/{name}/endpoint/protocols/openai/responses?api-version=v1` with `Foundry-Features: HostedAgents=V1Preview` — production mode

The request body carries the payload as the user message (`{"input": <json>, "stream": false}`); no `agent_reference`, `model`, or `agent` field is sent.

**Docker Compose mode** — `HOSTED_AGENT_*_URL` vars (defaults already in `docker-compose.yml`):

| Agent | Variable | Default |
|-------|----------|---------| 
| Clinical | `HOSTED_AGENT_CLINICAL_URL` | `http://agent-clinical:8088` |
| Compliance | `HOSTED_AGENT_COMPLIANCE_URL` | `http://agent-compliance:8088` |
| Coverage | `HOSTED_AGENT_COVERAGE_URL` | `http://agent-coverage:8088` |
| Synthesis | `HOSTED_AGENT_SYNTHESIS_URL` | `http://agent-synthesis:8088` |

Shared: `HOSTED_AGENT_TIMEOUT_SECONDS` (default `180`).

**Foundry Hosted Agents mode** — injected automatically by Bicep via `azd up`:

| Variable | Value |
|----------|-------|
| `AZURE_AI_PROJECT_ENDPOINT` | `https://<account>.services.ai.azure.com/api/projects/<project>` |
| `HOSTED_AGENT_CLINICAL_NAME` | `clinical-reviewer-agent` |
| `HOSTED_AGENT_COMPLIANCE_NAME` | `compliance-agent` |
| `HOSTED_AGENT_COVERAGE_NAME` | `coverage-assessment-agent` |
| `HOSTED_AGENT_SYNTHESIS_NAME` | `synthesis-agent` |

The backend acquires its bearer token with `DefaultAzureCredential` (the sync credential run off-thread via `asyncio.to_thread`, scope `https://ai.azure.com/.default`) — no manual token configuration needed.

The following RBAC roles are automatically assigned during `azd up`:

| **Role** | **Principal** | **Scope** | **How Assigned** | **Purpose** |
|----------|---------------|-----------|------------------|-------------|
| Cognitive Services OpenAI User | Backend Container App managed identity | Foundry account | `role-assignments.bicep` (provision) | Orchestrator calls the hosted-agent Responses endpoints |
| AcrPull | Foundry project managed identity | Container Registry | `role-assignments.bicep` (provision) | Foundry Agent Service pulls agent container images from ACR |
| Cognitive Services OpenAI Contributor | Foundry project managed identity | Foundry account | `role-assignments.bicep` (provision) | Hosted agent containers call gpt-5.4 via the Responses API |
| Azure AI User | Foundry project managed identity | Foundry account | `role-assignments.bicep` (provision) | Hosted agent containers use Foundry Agent Service data actions |
| Azure AI User | Deployer (user running `azd up`) | Foundry project | `az role assignment create` (postprovision hook) | `register_agents.py` registers agents via Foundry Agent Service API |
| Azure AI User | Backend Container App managed identity | Foundry project | `az role assignment create` (postprovision hook) | Backend calls Foundry Hosted Agents at runtime via `DefaultAzureCredential` |

The first four roles are assigned by `infra/modules/role-assignments.bicep` during `azd provision`. The remaining Azure AI User roles are assigned via `az role assignment create` in the postprovision hook — this is intentionally outside Bicep because the CLI command is natively idempotent (no error if the role was previously granted manually).

> **First-run note:** Azure RBAC propagation can take up to several minutes after a new role assignment. On the very first `azd up` (when the Azure AI User role is newly created), the postprovision hook automatically retries `register_agents.py` every 10 seconds (up to 12 attempts) until the permission propagates. On subsequent runs the role already exists and no retries are needed.

---

## Agent Registration

After `azd provision`, `scripts/register_agents.py` (called from the `azure.yaml` postprovision hook)
registers all four agents with Foundry:

1. Creates the five Foundry MCP tool connections (idempotent PUT via the ARM REST
   API) so they appear in the portal under **Build → Tools**.
2. Calls the `azure-ai-projects` SDK `client.agents.create_version()` with the ACR
   container image, the `responses@1.0.0` protocol record, per-agent
   `environment_variables` (including each agent's `TOOLBOX_ENDPOINT`), and CPU/memory.
3. Routes 100% of endpoint traffic to the new version via
   `client.beta.agents.patch_agent_details()` (data-plane SDK, no CLI extension
   required). Auto-start via `az cognitiveservices agent start` is a best-effort
   fallback — scale-to-zero agents also cold-start on the first request.

The two toolboxes (`clinical-tools`, `coverage-tools`) are created/verified
separately by `scripts/create_toolbox.py`.

Resource specs (defined in each `agents/<name>/agent.yaml`):

| Agent | CPU | Memory |
|-------|-----|--------|
| `clinical-reviewer-agent` | `1` | `2Gi` |
| `coverage-assessment-agent` | `1` | `2Gi` |
| `compliance-agent` | `0.5` | `1Gi` |
| `synthesis-agent` | `1` | `2Gi` |

---

## Agent IDs (Foundry)

| Agent ID | Module |
|----------|--------|
| `compliance-agent` | `agents/compliance/main.py` |
| `clinical-reviewer-agent` | `agents/clinical/main.py` |
| `coverage-assessment-agent` | `agents/coverage/main.py` |
| `synthesis-agent` | `agents/synthesis/main.py` |
