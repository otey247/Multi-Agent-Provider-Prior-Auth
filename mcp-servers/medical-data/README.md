# Medical-data MCP server

Self-hosted MCP **Streamable HTTP** server that replaces the retired DeepSense
MCP servers (`mcp.deepsense.ai`, now NXDOMAIN — see
[docs/troubleshooting.md](../../docs/troubleshooting.md)). It wraps official,
free, no-auth public health APIs so the prior-auth agents no longer depend on a
third-party hosted endpoint that can disappear.

| Path | Tools | Upstream API |
|------|-------|--------------|
| `/icd10/mcp` | `lookup_icd10`, `validate_icd10` | [NLM Clinical Tables ICD-10-CM](https://clinicaltables.nlm.nih.gov) |
| `/clinical_trials/mcp` | `search_clinical_trials` | [ClinicalTrials.gov API v2](https://clinicaltrials.gov/data-api/api) |
| `/npi/mcp` | `lookup_npi`, `search_npi` | [CMS NPPES NPI Registry](https://npiregistry.cms.hhs.gov) |
| `/cms_coverage/mcp` | `search_coverage` | [CMS Coverage API](https://api.coverage.cms.gov/docs/swagger/index.html) (NCD/LCD/Article — real determinations) |

PubMed (`pubmed.mcp.claude.com`) was unaffected by the outage and stays as-is.

The per-domain path layout mirrors DeepSense, so the agents need **only their
`MCP_*` URLs repointed** here — no agent code changes. Transport is
**stateless + JSON** (no session to expire), which is exactly what
agent_framework's `MCPStreamableHTTPTool` speaks.

## Run & test locally

```bash
pip install -r requirements.txt
python server.py                         # serves on :8080
python test_client.py                    # MCP smoke test against all 4 domains
```

`test_client.py` connects over MCP Streamable HTTP (the same client stack the
agents use), lists tools, and calls one tool per domain against the live
upstream APIs. Expect `5/5 tool calls returned live data`.

Docker:

```bash
docker build -t mcp-medical-data .
docker run -p 8080:8080 mcp-medical-data
curl localhost:8080/health             # {"status":"ok", ...}
```

## Deploy to Azure (integrated with `azd`)

This is wired into the standard deploy. On `azd up` / `azd provision`:

1. `infra/main.bicep` provisions the `mcp-medical-data` Container App and outputs
   `MEDICAL_MCP_BASE_URL` + `MCP_CONTAINER_APP_NAME`.
2. The `azure.yaml` postprovision hook builds the image
   (`az acr build ./mcp-servers/medical-data`) and points the app at it.
3. `scripts/register_agents.py` reads `MEDICAL_MCP_BASE_URL` and registers the
   clinical/coverage agents with `https://<fqdn>/<domain>/mcp` URLs.
4. `scripts/check_agents.py --runtime` verifies the agents end-to-end.

### Redeploy just this server

```bash
ACR=$(azd env get-value AZURE_CONTAINER_REGISTRY_ENDPOINT); ACR_NAME=${ACR%%.*}
TAG=$(date -u +%Y%m%d%H%M%S)
az acr build --registry "$ACR_NAME" --image "mcp-medical-data:$TAG" --platform linux/amd64 ./mcp-servers/medical-data
az containerapp update -n "$(azd env get-value MCP_CONTAINER_APP_NAME)" \
  -g "$(azd env get-value AZURE_RESOURCE_GROUP)" --image "$ACR/mcp-medical-data:$TAG" -o none
# Verify the deployed endpoint:
python mcp-servers/medical-data/test_client.py "$(azd env get-value MEDICAL_MCP_BASE_URL)"
```

## Notes & limitations

- **CMS coverage** uses the official [CMS Coverage API](https://api.coverage.cms.gov/docs/swagger/index.html)
  (MCIM). `search_coverage` returns real NCDs plus, when a US `state` is given,
  Local Coverage articles/LCDs — with per-code determinations: a diagnosis is
  `covered` (supports medical necessity), `not_covered` (excluded), or
  `not_listed`; a procedure is `addressed`/`not_addressed`. ICD-10 lists live on
  billing/coding **articles**; HCPCS lists on **LCDs** (esp. DME) — both are
  checked. A free license token (AMA/ADA/AHA click-through, fetched
  automatically) is required for CPT/HCPCS data; NCDs are public.
  **Matching is by policy title keywords** (the API has no code→policy reverse
  search), so pass good `keywords` and the patient `state`; when nothing matches
  confidently it falls back to the MCD search link + `manual_review`.
- **`billable` for ICD-10** is approximated as "exact match with no more-specific
  child code" (leaf node) from the NLM dataset.
- **Public ingress.** The Container App has external ingress so Foundry-hosted
  agents can reach it. It exposes only read-only public-government data and no
  secrets — the same posture as the DeepSense servers it replaces. Add
  authentication if you tighten that posture.
- **Resilience.** If this server is ever unreachable, the clinical/coverage
  agents now degrade to a valid HTTP 200 manual-review result instead of HTTP 500
  (startup reachability probe + handler fallback in each agent's `main.py`).
