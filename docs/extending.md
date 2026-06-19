# Extending the Application

## Add a New Agent

The multi-agent pipeline can be extended with additional agent roles (e.g., a
Pharmacy Benefits agent, Prior Treatment Verification agent, or Financial
Review agent). Each agent is a Foundry Hosted Agent served on the
`azure-ai-agentserver` Responses protocol host (`ResponsesAgentServerHost`,
protocol version `1.0.0`) against the `gpt-5.4` model.

There are two agent styles in the solution — pick the one that matches your
agent's needs:

- **Tool-using agent** (like `clinical-reviewer-agent` / `coverage-assessment-agent`):
  drives the model with the OpenAI SDK directly — `client.responses.parse(text_format=<PydanticModel>)`
  against `{AZURE_AI_PROJECT_ENDPOINT}/openai/v1` — and consumes MCP tools through
  a **Foundry Toolbox** (see *Add a New MCP Server*). It inlines its
  `skills/<name>/SKILL.md` into the system prompt at startup.
- **Reasoning-only agent** (like `compliance-agent` / `synthesis-agent`): uses the
  Microsoft Agent Framework (`agent_framework`) — `AzureOpenAIResponsesClient`
  for the model call and `SkillsProvider` to load its SKILL.md, with
  `default_options={"response_format": <PydanticModel>}` for structured output.
  No tools.

Both styles authenticate with `DefaultAzureCredential` (managed identity in the
hosted runtime, `az` CLI credential locally) — no API keys.

**Step 1 — Agent container** (`agents/new-agent/main.py` + `agents/new-agent/schemas.py`):

Create a new agent container following the same pattern as the four existing agents:

**`agents/new-agent/schemas.py`** — declare the structured output model:

```python
from pydantic import BaseModel
from typing import Optional

class NewAgentResult(BaseModel):
    status: str
    findings: list[str]
    confidence: int
    summary: Optional[str] = None
```

**`agents/new-agent/main.py`** — for a **reasoning-only agent** (Microsoft Agent Framework), follow `agents/compliance/main.py` / `agents/synthesis/main.py`:

```python
import inspect
import json
import os
from pathlib import Path
from typing import Any

from agent_framework import SkillsProvider
from agent_framework.azure import AzureOpenAIResponsesClient
from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponsesAgentServerHost,
    TextResponse,
)
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from schemas import NewAgentResult

load_dotenv(override=True)  # override=True required for Foundry-deployed env vars


def main() -> None:
    skills_provider = SkillsProvider(
        skill_paths=str(Path(__file__).parent / "skills")
    )

    # default_options enforces NewAgentResult schema on every agent.run() call
    # made by the Responses protocol handler — token-level JSON constraint.
    agent = AzureOpenAIResponsesClient(
        project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
        deployment_name=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
        credential=DefaultAzureCredential(),
    ).as_agent(
        name="new-agent",
        id="new-agent",  # Must match registered agent name for Foundry Traces correlation
        instructions="You are a ... agent for prior authorization requests.",
        tools=[],
        context_providers=[skills_provider],
        default_options={"response_format": NewAgentResult},
    )

    # agentserver-core 2.x serves the Responses protocol via ResponsesAgentServerHost.
    app = ResponsesAgentServerHost()

    @app.response_handler
    async def handle_response(request: CreateResponse, context: ResponseContext, cancellation_signal):
        input_text = await context.get_input_text() or ""
        result = agent.run(input_text)
        if inspect.isawaitable(result):
            result = await result
        # extract JSON from the run result (see _agent_result_to_text in the
        # existing agents for the full helper)
        return TextResponse(context, request, text=result.model_dump_json())

    app.run()


if __name__ == "__main__":
    main()
```

For a **tool-using agent** (OpenAI SDK + Foundry Toolbox), follow
`agents/clinical/main.py` / `agents/coverage/main.py`: inline the SKILL.md into
the system prompt, resolve the toolbox endpoint from `TOOLBOX_ENDPOINT`, open the
managed-identity-authenticated `AsyncOpenAI(base_url={AZURE_AI_PROJECT_ENDPOINT}/openai/v1, api_key=<bearer token>)`
client, and call `run_with_toolbox(...)` (in `agents/clinical/mcp_toolbox.py`,
copied per tool-using agent) which drives `client.responses.parse(text_format=<PydanticModel>)`
in a tool-calling loop against the toolbox's tools. Wrap the run so any failure
degrades to a schema-valid HTTP 200 fallback (the hosted runtime must never see a 500).

Key conventions:
- `schemas.py` declares the Pydantic output model; structured output is enforced at the token level (no JSON-fence parsing) — via `default_options={"response_format": ...}` for MAF agents, or `responses.parse(text_format=...)` for OpenAI-SDK agents
- MAF agents import `AzureOpenAIResponsesClient` from `agent_framework.azure`; its constructor takes `project_endpoint` + `deployment_name` + `credential=DefaultAzureCredential()`, and `name`/`instructions`/`tools`/`context_providers`/`default_options` go on `.as_agent()`
- `SkillsProvider(skill_paths=.../skills)` loads SKILL.md for MAF agents; OpenAI-SDK agents inline the SKILL.md text directly into the system prompt at startup
- Tool-using agents consume MCP tools through a **Foundry Toolbox** (an MCP endpoint on the project domain), not by calling public servers directly — see *Add a New MCP Server*
- `load_dotenv(override=True)` is required — `override=True` ensures Foundry-injected env vars take precedence
- The agent is served as a `POST .../responses` HTTP endpoint by `ResponsesAgentServerHost().run()`
- The agent's `id` / `name` must match the name used in `scripts/register_agents.py` so Foundry Traces correlate
- `main()` bridges the `MONITORING_CONNECTION_STRING` env var to `APPLICATIONINSIGHTS_CONNECTION_STRING` and sets `OTEL_SERVICE_NAME` for telemetry
- Agents that need upstream results receive them as JSON in the request payload

**Step 2 — SKILL.md** (`agents/new-agent/skills/new-agent/SKILL.md`):

```markdown
# [Role Name] Skill

## Description
One-liner describing what this agent does.

## Instructions
[Same content as NEW_AGENT_INSTRUCTIONS — keep synced]

### Available MCP Tools (if applicable)
- `tool_name(param)` — Description (the model sees toolbox tools as `{server_label}___{tool_name}`)

### Output Format
Return JSON:
{
    "field": "value"
}

### Quality Checks
Before completing, verify:
- [ ] All required fields present in output
- [ ] Output is valid JSON

### Common Mistakes to Avoid
- Do NOT generate fake data when a tool call fails
- Do NOT make final approval/denial decisions (synthesis agent does that)
```

**Step 3 — MCP tools** (Foundry Toolbox):

If the agent uses MCP tools, expose them through a **Foundry Toolbox** rather than
registering tools on the agent itself (the agent is always registered with
`tools=[]`). See *Add a New MCP Server* below for the full flow: add the tool to a
reachable MCP server, declare a toolbox in `scripts/create_toolbox.py`, and inject
the toolbox's `TOOLBOX_ENDPOINT` into the agent's env in `scripts/register_agents.py`.
`scripts/register_agents.py` also creates MCP project connections (in `MCP_CONNECTIONS`)
for portal visibility under **Build → Tools**, created idempotently during `azd up`.

**Step 4 — Orchestrator** (`backend/app/agents/orchestrator.py`):

Import and register the agent in `run_multi_agent_review()`:

```python
from app.agents.new_agent import run_new_review
```

The pipeline has four phases:

```
Phase 1 (parallel):   Compliance + Clinical  → asyncio.gather()
Phase 2 (sequential): Coverage (needs Clinical findings)
Phase 3 (synthesis):  Reasoning-only, all results as input
Phase 4 (audit):      Build audit trail + justification PDF
```

To add a parallel agent:
```python
new_task = asyncio.create_task(
    _safe_run("New Agent", run_new_review, request_data)
)
compliance_result, clinical_result, new_result = await asyncio.gather(
    compliance_task, clinical_task, new_task
)
```

To add a sequential agent:
```python
new_result = await _safe_run(
    "New Agent", run_new_review, request_data, clinical_result
)
```

**Step 5 — Synthesis prompt** (`backend/app/agents/orchestrator.py`):

Add the new agent's output to the synthesis prompt:

```python
prompt = f"""...existing synthesis prompt...

--- NEW AGENT REPORT ---
{json.dumps(new_result, indent=2, default=str)}

--- END REPORTS ---
..."""
```

**Step 6 — SSE progress events** (`backend/app/agents/orchestrator.py`):

Add the new agent to progress event emissions:

```python
await _emit({
    "phase": "phase_1",
    "agents": {
        "compliance": {"status": "running", "detail": "..."},
        "clinical": {"status": "running", "detail": "..."},
        "new_agent": {"status": "running", "detail": "Starting..."},
    },
})
```

Update `frontend/lib/types.ts` and `ProgressTracker` for the new agent.

**Step 7 — Audit trail and PDF** (optional):

Update `_build_audit_trail()`, `_generate_audit_justification()`, and
`generate_audit_justification_pdf()` for the new agent's data.

**Summary of files touched:**

| File | Change |
|------|--------|
| `agents/new-agent/main.py` | New file: Responses host (`ResponsesAgentServerHost`) wiring. MAF (`AzureOpenAIResponsesClient` + `SkillsProvider`) for a reasoning-only agent, or OpenAI SDK + `mcp_toolbox.run_with_toolbox` for a tool-using agent |
| `agents/new-agent/mcp_toolbox.py` | New file (tool-using agents only): per-request Foundry Toolbox MCP client + `responses.parse` tool-calling loop (copy from `agents/clinical/mcp_toolbox.py`) |
| `agents/new-agent/schemas.py` | New file: Pydantic output model (must match SKILL.md output format exactly — enforced at token level) |
| `agents/new-agent/skills/new-agent/SKILL.md` | New file: skill instructions |
| `agents/new-agent/Dockerfile` | New file: container image |
| `agents/new-agent/requirements.txt` | New file. MAF agent: `agent-framework-core`, `agent-framework-azure-ai`, `azure-ai-agentserver-core==2.0.0b6`, `azure-ai-agentserver-responses==1.0.0b7`, `azure-identity`, `python-dotenv`. Tool-using agent: `azure-ai-agentserver-core==2.0.0b6`, `azure-ai-agentserver-responses==1.0.0b7`, `openai`, `mcp`, `azure-identity`, `pydantic`, `python-dotenv` |
| `docker-compose.yml` | Add new agent service + env vars |
| `agents/new-agent/agent.yaml` | New file: Foundry Hosted Agent descriptor (name, runtime, resources, env vars) |
| `scripts/register_agents.py` | Add new agent to the registration list (with `tools=[]`, env incl. `TOOLBOX_ENDPOINT` if tool-using); add any new MCP project connections |
| `scripts/create_toolbox.py` | Add/extend a Foundry Toolbox (tool-using agents only) |
| `backend/app/models/schemas.py` | Add matching Pydantic model (must stay in sync with `agents/new-agent/schemas.py`) |
| `azure.yaml` | Add `az acr build` call for the new agent image in the postprovision hook |
| `backend/app/config.py` | Add `HOSTED_AGENT_NEW_NAME` setting (Foundry agent name) and optionally `NEW_AGENT_URL` (docker-compose URL) |
| `backend/app/services/hosted_agents.py` | Add dispatch call for new agent |
| `backend/app/agents/orchestrator.py` | Import, phase registration, synthesis prompt, SSE events |
| `frontend/lib/types.ts` | Add agent ID to types |
| `frontend/components/progress-tracker.tsx` | Render new agent status |
| `backend/app/services/audit_pdf.py` | Render new agent data in PDF (optional) |

---

## Add a New MCP Server

Hosted Foundry agents can reach the Foundry **project domain** but not arbitrary
public internet, so MCP tools are consumed through a **Foundry Toolbox**: a
managed MCP endpoint on the project domain (`{project_endpoint}/toolboxes/{name}/mcp?api-version=v1`)
that Foundry proxies out to the real MCP servers from its own network. The
tool-using agents (`clinical`/`coverage`) connect to the toolbox as MCP clients
with the managed-identity bearer token plus the header
`Foundry-Features: Toolboxes=V1Preview`. Tools are exposed to the model as
`{server_label}___{tool_name}` (e.g. `icd10___validate_code`).

The backing MCP servers are the self-hosted **medical-data** server
(`mcp-servers/medical-data/server.py`, deployed as an Azure Container App; wraps
free public APIs — NLM Clinical Tables for ICD-10, ClinicalTrials.gov v2, CMS
NPPES for NPI, and the CMS Coverage API) and **PubMed**
(`https://pubmed.mcp.claude.com/mcp`, unauthenticated).

**Step 1 — Add the tool to a backing MCP server** (`mcp-servers/medical-data/server.py`):

Add a `@<domain>.tool()`-decorated async function to the relevant `FastMCP`
instance (or mount a new domain on its own `/<path>/mcp` route). Each tool wraps
a reachable upstream API and returns a JSON-serializable dict:

```python
@cms.tool()
async def lookup_cpt(code: str) -> dict[str, Any]:
    """Get description and RVU value for a CPT/HCPCS code."""
    try:
        ...
    except Exception as exc:  # noqa: BLE001
        return _err({"code": code}, exc)
```

(If the tool lives on a different reachable MCP server, point the toolbox at that
server's URL in Step 2 instead.)

**Step 2 — Add it to a Foundry Toolbox** (`scripts/create_toolbox.py`):

Add the backing server to the appropriate toolbox in `_toolboxes()` (or create a
new toolbox). The medical-data domains are referenced via the
`MEDICAL_MCP_BASE_URL` base:

```python
return {
    "coverage-tools": [
        _mcp("npi", f"{base}/npi/mcp"),
        _mcp("cms_coverage", f"{base}/cms_coverage/mcp"),
        # new server backing the toolbox:
        _mcp("cpt_validator", f"{base}/cpt_validator/mcp"),
    ],
    ...
}
```

Run `python scripts/create_toolbox.py` to create/update + verify the toolbox.

**Step 3 — Wire the toolbox into the consuming agent** (`scripts/register_agents.py`):

The agent already receives `TOOLBOX_ENDPOINT` (its toolbox MCP URL) in its env
dict; once a tool is in that toolbox it is discovered automatically (the agent
lists the toolbox's tools at request time). If you created a *new* toolbox, set
the agent's `TOOLBOX_ENDPOINT` to it. Optionally add a corresponding entry to
`MCP_CONNECTIONS` for portal visibility under **Build → Tools**.

**Step 4 — SKILL.md** (`agents/<target-agent>/skills/<skill-name>/SKILL.md`):

Document the new tool and update the agent's `_BASE_INSTRUCTIONS` server/tool
list in `agents/<target-agent>/main.py` so the model knows it exists:

```markdown
#### CPT Validator (cpt_validator)
- `validate_cpt(code)` — Check if CPT code is valid
- `lookup_cpt(code)` — Get description and RVU value
```

**Step 5 — Orchestrator** (only if adding a new agent role).

**Architecture summary:**

```
mcp-servers/medical-data/server.py   → tool implementation (wraps a public API)
scripts/create_toolbox.py            → Foundry Toolbox that proxies to the server
scripts/register_agents.py           → TOOLBOX_ENDPOINT env var + MCP_CONNECTIONS (portal)
agents/<agent>/main.py               → consumes the toolbox as an MCP client per request
agents/<agent>/skills/*/SKILL.md     → usage instructions for the agent
backend/app/agents/orchestrator.py   → pipeline phases (only if adding a new agent role)
```

---

## Change the Decision Rubric

Edit the synthesis agent's SKILL.md:

```
agents/synthesis/skills/synthesis-decision/SKILL.md
```

Domain experts can update the gate criteria, confidence weights, and decision thresholds without touching any Python code.

---

## Customize Notification Letters

Edit `backend/app/services/notification.py`. The `generate_approval_letter()`
and `generate_pend_letter()` functions accept parameters and produce structured
text. The `generate_letter_pdf()` function renders a professionally formatted
PDF using `fpdf2`.

---

## Add CPT/HCPCS Codes to the Lookup Table

Edit `_KNOWN_CODES` in `backend/app/services/cpt_validation.py`.

---

## Use MCP with Foundry Hosted Agents

Tool-using agents consume MCP tools through a **Foundry Toolbox** — an MCP
endpoint on the project domain that proxies to the backing servers. The agent
connects to the toolbox as an MCP **client** (the toolbox endpoint cannot be
passed to the Responses API as a `type: mcp` `server_url`). To test a toolbox
directly, open an MCP session against its endpoint with the managed-identity
bearer token and the preview header — the same pattern `scripts/create_toolbox.py`
uses to verify each toolbox:

```python
from azure.identity import DefaultAzureCredential
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

credential = DefaultAzureCredential()
token = credential.get_token("https://ai.azure.com/.default").token
url = f"{PROJECT_ENDPOINT}/toolboxes/coverage-tools/mcp?api-version=v1"
headers = {"Authorization": f"Bearer {token}", "Foundry-Features": "Toolboxes=V1Preview"}

async with streamablehttp_client(url, headers=headers) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        names = [t.name for t in (await session.list_tools()).tools]
        result = await session.call_tool("npi___npi_validate", {"npi": "1912084401"})
        print(names, result.content[0].text)
```

The agent then drives the model with `client.responses.parse(text_format=<PydanticModel>)`
in a tool-calling loop, calling the toolbox's tools as the model requests them
(see `agents/clinical/mcp_toolbox.py`). The agents themselves are registered with
`tools=[]`; the toolbox endpoint is injected via the `TOOLBOX_ENDPOINT` env var.

---

## Future Enhancement: Azure AI Search for Policy RAG

The current system retrieves coverage policies at runtime via the **CMS Coverage MCP server**, which provides Medicare LCDs and NCDs. This works well for Medicare cases but has limitations — the Synthesis agent already flags this with a disclaimer:

> *"Coverage policies reflect Medicare LCDs/NCDs only. If this review is for a commercial or Medicare Advantage plan, payer-specific policies may differ."*

**Azure AI Search** with vector indexing could significantly enhance the system by enabling semantic retrieval over a broader set of policy documents. Below are the opportunities, organized by which agent would benefit.

### Where AI Search Adds Value

| Agent | Index Content | What It Enables |
|-------|--------------|-----------------|
| **Coverage Agent** | Commercial payer PA policies (UHC, Aetna, BCBS, Cigna, etc.) | Payer-specific coverage criteria instead of Medicare-only. E.g., "UHC requires 6 weeks of conservative therapy before approving spinal fusion." |
| **Coverage Agent** | Medicare Advantage plan-specific supplements | Plan-level nuances beyond standard Medicare LCDs/NCDs |
| **Clinical Agent** | Clinical practice guidelines (ACR Appropriateness Criteria, NCCN, AUA, etc.) | Evidence-based clinical reasoning beyond what PubMed MCP returns — structured guidelines rather than raw literature |
| **Compliance Agent** | Organization-specific PA submission requirements | Internal checklists, required documentation templates, payer-specific form requirements |
| **Synthesis Agent** | Historical PA decisions (vectorized) | Precedent-based reasoning — "95% of similar cases with this diagnosis and procedure were approved" |

### How It Would Work

Azure AI Search would be exposed as an **MCP tool** (or direct SDK call) that agents query during their review:

```
Coverage Agent prompt → "Search payer policies for CPT 22630 with UnitedHealthcare"
                      → AI Search vector query → top-k relevant policy chunks
                      → Agent reasons over retrieved policy text
```

Each index would use:
- **Vector embeddings** (Azure OpenAI `text-embedding-3-large`) for semantic search
- **Hybrid search** (vector + keyword) for policy ID lookups
- **Metadata filters** (payer name, effective date, procedure category) for precision

### What You Would Need

| Requirement | Details |
|-------------|---------|
| **Policy documents** | PDFs or structured text from commercial payers. These are typically proprietary and obtained through payer contracts or provider portals. |
| **Azure AI Search resource** | Standard tier or higher for vector search support |
| **Embedding model** | An Azure OpenAI embedding deployment (e.g., `text-embedding-3-large`) in the same region |
| **Ingestion pipeline** | Document chunking, embedding, and indexing — can use Azure AI Search's built-in [integrated vectorization](https://learn.microsoft.com/en-us/azure/search/vector-search-integrated-vectorization) or a custom pipeline |
| **MCP server or tool wrapper** | Expose the search index as a tool the agents can call |

### What It Does NOT Replace

AI Search is a **retrieval** layer — it complements, not replaces, the existing MCP tools:

| Data Source | Keep Using | Why |
|-------------|-----------|-----|
| CMS Coverage MCP | ✅ | Live, authoritative Medicare LCD/NCD data |
| NPI Registry MCP | ✅ | Real-time provider verification |
| ICD-10 MCP | ✅ | Code validation and lookup |
| PubMed MCP | ✅ | Current medical literature |
| Clinical Trials MCP | ✅ | Active trial matching |

AI Search would add a **sixth data source** — payer policy documents — not replace the existing five.

### Implementation Priority

This enhancement is most valuable when:
1. The system needs to handle **commercial payer** cases (not just Medicare)
2. The organization has **access to payer policy documents** to index
3. There is a need for **historical decision** consistency across reviewers

Until policy documents are available for ingestion, the current CMS-only approach is appropriate for the demo scope.
