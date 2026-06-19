# Production Migration Path

The demo uses an in-memory Python dictionary for review storage and returns
generated PDFs inline as base64. When moving to production, two services
need to be introduced.

## Current Demo Architecture

| Concern | Demo Approach | Limitation |
|---------|--------------|------------|
| Review persistence | `_review_store` dict in `orchestrator.py` | Lost on restart; single-process |
| Decision storage | Same in-memory dict | Same as above |
| Generated PDFs | Base64 in JSON response | No long-term storage |
| Medical documents | Pasted into text field | No file upload |
| Audit trail | Embedded in response JSON | Not independently queryable |

## Why the Migration Is Straightforward

The store layer is abstracted behind four functions in `orchestrator.py`:

```python
store_review(request_id, request_data, response)
get_review(request_id)
list_reviews()
store_decision(request_id, decision)
```

No other module touches `_review_store` directly.

---

## PostgreSQL — Structured Data

Use PostgreSQL (or Azure Database for PostgreSQL — Flexible Server).

### Suggested Schema

```sql
CREATE TABLE reviews (
    request_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_name  TEXT NOT NULL,
    patient_dob   DATE NOT NULL,
    provider_npi  VARCHAR(10) NOT NULL,
    insurance_id  TEXT,
    diagnosis_codes TEXT[] NOT NULL,
    procedure_codes TEXT[] NOT NULL,
    clinical_notes TEXT NOT NULL,
    request_data  JSONB NOT NULL,
    response_data JSONB NOT NULL,
    recommendation VARCHAR(20) NOT NULL,
    confidence    NUMERIC(3,2),
    confidence_level VARCHAR(6),
    audit_justification TEXT,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE decisions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id       UUID NOT NULL REFERENCES reviews(request_id),
    action          VARCHAR(20) NOT NULL,
    override_decision VARCHAR(20),
    override_rationale TEXT,
    auth_number     VARCHAR(30) NOT NULL,
    letter_text     TEXT NOT NULL,
    letter_pdf_key  TEXT,
    decided_by      TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT one_decision_per_review UNIQUE (review_id)
);

CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    review_id   UUID NOT NULL REFERENCES reviews(request_id),
    event_type  VARCHAR(50) NOT NULL,
    event_data  JSONB NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_reviews_created ON reviews(created_at DESC);
CREATE INDEX idx_reviews_recommendation ON reviews(recommendation);
CREATE INDEX idx_reviews_provider ON reviews(provider_npi);
CREATE INDEX idx_audit_log_review ON audit_log(review_id);
```

### Migration Steps

1. Add `asyncpg` to `requirements.txt`
2. Add `DATABASE_URL` environment variable
3. Create `backend/app/services/database.py`
4. Update `orchestrator.py` imports
5. Run schema migration
6. Update `decision.py` for blob storage keys

---

## Azure Blob Storage — Unstructured Documents

### Container Layout

```
prior-auth-documents/
├── uploads/              # Original medical documents
│   └── {review_id}/
├── letters/              # Generated notification PDFs
│   └── {review_id}/
│       └── {auth_number}.pdf
└── audit/                # Archived audit justification docs
    └── {review_id}/
        └── audit-justification.md
```

### Documents Table

```sql
CREATE TABLE documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id   UUID NOT NULL REFERENCES reviews(request_id),
    doc_type    VARCHAR(30) NOT NULL,
    filename    TEXT NOT NULL,
    blob_url    TEXT NOT NULL,
    content_type TEXT,
    size_bytes  BIGINT,
    uploaded_by TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_documents_review ON documents(review_id);
```

### Integration Steps

1. Add `azure-storage-blob` to `requirements.txt`
2. Add `AZURE_STORAGE_CONNECTION_STRING`
3. Create `backend/app/services/blob_storage.py`
4. Upload PDFs after generation
5. Store blob key in `decisions.letter_pdf_key`
6. Add `GET /api/documents/{review_id}` endpoint

---

## Additional Dependencies

| Package | Purpose |
|---------|---------|
| `asyncpg` | Async PostgreSQL driver |
| `sqlalchemy[asyncio]` | ORM layer (optional) |
| `alembic` | Database schema migrations |
| `azure-storage-blob` | Azure Blob Storage SDK |
| `azure-identity` | Managed identity auth |

## Environment Variables

```bash
# PostgreSQL
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/priorauth

# Azure Blob Storage — prefer managed identity (backend Container App has system-assigned identity)
AZURE_STORAGE_ACCOUNT_URL=https://<account>.blob.core.windows.net
# Fall back to connection string only if managed identity is not available:
# AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;...
```

---

## Azure API Management — MCP Gateway

### Why APIM for MCP?

The clinical and coverage agents reach their MCP tools through Foundry
Toolboxes, which proxy to the self-hosted **medical-data** MCP server (an Azure
Container App, see `mcp-servers/medical-data/server.py`) and to PubMed. The
medical-data server wraps several upstream public APIs — NLM Clinical Tables,
ClinicalTrials.gov, CMS NPPES, and the CMS Coverage API. In production those
upstream calls and the public ingress on the Container App create operational
considerations an MCP-aware gateway can centralize:

- **No central rate limiting** — a misbehaving or hallucinating agent can flood an upstream public API (or the medical-data Container App) with unlimited requests.
- **No fallback** — if an upstream API or the medical-data server is degraded, callers fail with no circuit-breaker protection.
- **No infrastructure-level audit trail** — MCP tool calls are visible only in application logs, not at the network edge.
- **Direct public ingress** — the medical-data Container App is exposed over its public FQDN; fronting it lets you restrict ingress and centralize TLS/observability.

Azure API Management's **native MCP Gateway** feature
([docs](https://learn.microsoft.com/en-us/azure/api-management/expose-existing-mcp-server))
addresses these by acting as a protocol-aware proxy in front of the medical-data
MCP Container App (and PubMed). Because APIM natively speaks the MCP protocol
(Streamable HTTP and SSE transport), it handles the transport lifecycle without
custom buffering policies.

### Supported APIM Tiers

The MCP Gateway feature is **not** available on the Consumption tier.
Supported tiers (per [official documentation](https://learn.microsoft.com/en-us/azure/api-management/expose-existing-mcp-server)):

| Tier | MCP Gateway | Provisioning Time | Recommended For |
|---|---|---|---|
| Developer | ✅ | 30-60 min | Local dev/test (no SLA) |
| Basic v2 | ✅ | ~5-10 min | Cost-sensitive production |
| **Standard v2** | ✅ | ~5-10 min | **Recommended** — VNet support, balanced cost |
| Premium v2 | ✅ | ~5-10 min | Multi-region, high scale |
| Consumption | ❌ | — | Not supported |

### Deployment Strategy: Pre-Provision APIM

> **Important:** Standard v2 provisioning takes ~5-10 minutes. If your
> `azd up` pipeline must complete in under 10 minutes, **pre-provision
> the APIM instance separately** so subsequent deployments reference
> the existing resource and add only seconds to the pipeline.

**Step 1 — One-time APIM provisioning (run once, outside of `azd up`):**

```bash
# Create the APIM instance separately (takes ~5-10 min for Standard v2)
az apim create \
  --name <apim-name> \
  --resource-group <rg-name> \
  --location <region> \
  --sku-name StandardV2 \
  --publisher-name "Contoso Health" \
  --publisher-email admin@contoso.com
```

**Step 2 — Reference in Bicep as `existing`:**

```bicep
// infra/modules/apim.bicep — references the pre-provisioned instance
resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimName
  scope: resourceGroup()
}
```

This way `azd up` only creates the MCP server entries and policies on the
already-running APIM instance — no provisioning wait time.

### Architecture

The clinical and coverage agents connect to Foundry Toolboxes, which would
proxy to APIM instead of directly to the medical-data Container App:

```
MAF Hosted Agents (Azure AI Foundry)
  ├── agent-clinical  ──► clinical-tools toolbox ──┐
  ├── agent-coverage  ──► coverage-tools toolbox ──┤──► APIM MCP Gateway (https://<apim>.azure-api.net/)
  ├── agent-compliance  (no tools)                 │
  └── agent-synthesis   (no tools)                 │
                                  │── /icd10-mcp/mcp   → <medical-data ACA>/icd10/mcp
                                  │── /pubmed-mcp/mcp  → pubmed.mcp.claude.com/mcp
                                  │── /trials-mcp/mcp  → <medical-data ACA>/clinical_trials/mcp
                                  │── /npi-mcp/mcp     → <medical-data ACA>/npi/mcp
                                  └── /cms-mcp/mcp     → <medical-data ACA>/cms_coverage/mcp
```

`<medical-data ACA>` is the medical-data MCP Container App FQDN, surfaced as the
`MEDICAL_MCP_BASE_URL` Bicep output. Only the toolbox backing URLs in
`scripts/create_toolbox.py` change to point at APIM; the agents continue to call
their toolboxes unchanged.

### What APIM MCP Gateway Adds

| Capability | How |
|---|---|
| **Native MCP protocol** | APIM speaks MCP natively — no custom streaming/buffering policies needed |
| **Centralized header injection** | Any per-backend headers managed via `<set-header>` policy rather than in server/agent code |
| **Rate limiting** | `<rate-limit-by-key>` policy per MCP backend, keyed by `Mcp-Session-Id` |
| **Circuit breaker** | `<retry>` + mock policy fallback if the medical-data server or an upstream API goes down |
| **Upstream swap** | Change the APIM backend URL without re-creating the toolboxes |
| **Centralised monitoring** | All MCP call volume, latency and failures in one App Insights dashboard |
| **Network isolation** | The medical-data Container App fronts a private APIM endpoint; restrict its public ingress |

### Step-by-Step Setup

#### 1. Register MCP Backends in APIM

For each MCP backend, create an MCP Server entry in APIM. The medical-data
domains share one Container App FQDN (`MEDICAL_MCP_BASE_URL`); PubMed is a
separate external endpoint. This can be done via the Azure Portal or Bicep:

**Portal:**
1. Navigate to your APIM instance → **APIs** → **MCP Servers** → **+ Create MCP server**
2. Select **Expose an existing MCP server**
3. Enter the backend MCP server base URL (e.g. `https://<medical-data-aca-fqdn>/icd10/mcp`)
4. Set Transport type to **Streamable HTTP**
5. Enter a Name (e.g. `icd10-codes`) and Base path (e.g. `icd10-mcp`)
6. Click **Create**

Repeat for each medical-data domain (ClinicalTrials, NPI, CMS Coverage) plus PubMed.

**Bicep (automated via `azd up`):**

```bicep
// infra/modules/apim-mcp.bicep

// MCP Server for ICD-10 codes (medical-data Container App domain)
resource icd10McpServer 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apim
  name: 'icd10-mcp'
  properties: {
    displayName: 'ICD-10 Codes MCP'
    path: 'icd10-mcp'
    protocols: ['https']
    type: 'mcp'
    serviceUrl: '${medicalMcpBaseUrl}/icd10/mcp'
  }
}

// Repeat for clinical_trials, npi, cms_coverage (all on the medical-data ACA)
// and pubmed (https://pubmed.mcp.claude.com/mcp)
```

#### 2. Configure Policies (Header Injection, Optional)

The medical-data Container App and PubMed need no special headers. If you place
the medical-data ACA behind APIM and restrict its public ingress, use an inbound
`<set-header>` policy for any auth/identity headers your ingress requires:

```xml
<policies>
    <inbound>
        <base />
        <!-- Example: forward an APIM-managed identity/auth header to the backend -->
        <!-- <set-header name="X-Backend-Auth" exists-action="override">
            <value>{{backend-auth-named-value}}</value>
        </set-header> -->
    </inbound>
    <backend>
        <base />
    </backend>
    <outbound>
        <base />
    </outbound>
    <on-error>
        <base />
    </on-error>
</policies>
```

#### 3. Configure Rate Limiting (Optional but Recommended)

Add per-session rate limiting to prevent runaway agent loops:

```xml
<inbound>
    <base />
    <set-variable name="body" value="@(context.Request.Body.As<string>(preserveContent: true))" />
    <choose>
        <when condition="@(
            Newtonsoft.Json.Linq.JObject.Parse((string)context.Variables[&quot;body&quot;])[&quot;method&quot;] != null
            && Newtonsoft.Json.Linq.JObject.Parse((string)context.Variables[&quot;body&quot;])[&quot;method&quot;].ToString() == &quot;tools/call&quot;
        )">
            <rate-limit-by-key
                calls="10"
                renewal-period="60"
                counter-key="@(context.Request.Headers.GetValueOrDefault(&quot;Mcp-Session-Id&quot;, &quot;unknown&quot;))" />
        </when>
    </choose>
</inbound>
```

#### 4. Repoint the Toolbox Backing URLs

The clinical and coverage agents always connect to their Foundry Toolbox on the
project domain — that does not change. What changes is where the toolboxes proxy
to. The backing MCP URLs are built in `scripts/create_toolbox.py` from
`MEDICAL_MCP_BASE_URL` (medical-data domains) and `MCP_PUBMED`. Point those at
APIM and re-run the script to create a new toolbox version:

```python
# scripts/create_toolbox.py — backing URLs now resolve to APIM
base = os.environ["MEDICAL_MCP_BASE_URL"]   # set to https://<apim>.azure-api.net
pubmed = os.environ.get("MCP_PUBMED", "https://<apim>.azure-api.net/pubmed-mcp/mcp")
# _mcp("icd10", f"{base}/icd10-mcp/mcp"), etc.
```

```bash
# Recreate the toolbox versions against the APIM-fronted backends, then verify:
python scripts/create_toolbox.py
python scripts/create_toolbox.py --verify
```

#### 5. No Agent Code Changes Required

The agents reach tools through the toolbox (`TOOLBOX_ENDPOINT`), so fronting the
backends with APIM is transparent to the agent containers — no agent image
rebuild is needed. The medical-data MCP server (`mcp-servers/medical-data/server.py`)
also stays unchanged; APIM simply sits in front of its public ingress.

### MCP Backend URL Mapping

These are the backing URLs the toolboxes proxy to (configured in
`scripts/create_toolbox.py`); the agents themselves only ever see the toolbox
endpoint.

| Toolbox tool | Current backing URL (direct) | APIM MCP Gateway value |
|---|---|---|
| `icd10` | `${MEDICAL_MCP_BASE_URL}/icd10/mcp` | `https://<apim>.azure-api.net/icd10-mcp/mcp` |
| `pubmed` | `https://pubmed.mcp.claude.com/mcp` | `https://<apim>.azure-api.net/pubmed-mcp/mcp` |
| `clinical_trials` | `${MEDICAL_MCP_BASE_URL}/clinical_trials/mcp` | `https://<apim>.azure-api.net/trials-mcp/mcp` |
| `npi` | `${MEDICAL_MCP_BASE_URL}/npi/mcp` | `https://<apim>.azure-api.net/npi-mcp/mcp` |
| `cms_coverage` | `${MEDICAL_MCP_BASE_URL}/cms_coverage/mcp` | `https://<apim>.azure-api.net/cms-mcp/mcp` |

### Diagnostic Logging Caveat

> **Important:** If you enable Application Insights diagnostic logging at
> the global scope (All APIs) for your APIM instance, set the **Number of
> payload bytes to log** for **Frontend Response** to `0`. This prevents
> response body logging from interfering with MCP streaming transport.
> Configure payload logging selectively at the individual MCP server scope
> if needed.

### Limitations (as of March 2026)

- The backing MCP server must conform to MCP version `2025-06-18` or later. The medical-data server speaks MCP Streamable HTTP (stateless, JSON responses).
- APIM MCP Gateway supports MCP **tools** and **resources**, but does **not** support MCP **prompts** (which is fine — the medical-data server and PubMed expose only tools).
- APIM does not display tools from the existing MCP server in the portal; tools are registered and managed on the backing server.
- MCP server capabilities are not supported in APIM [Workspaces](https://learn.microsoft.com/en-us/azure/api-management/workspaces-overview).

---

## What NOT to Change

- **Agent containers** — the four MAF Hosted Agent containers (clinical, coverage, compliance, synthesis) call the Foundry Responses API and return JSON. They are completely unaware of the backend's storage layer.
- **Frontend** — the API contract stays the same
- **MCP server configuration** — independent of storage
- **Notification letter templates** — produce same output regardless of storage
