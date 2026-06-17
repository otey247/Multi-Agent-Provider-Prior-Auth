# Medical-data MCP server

Self-hosted MCP **Streamable HTTP** server that replaces the retired DeepSense
MCP servers (`mcp.deepsense.ai`, now NXDOMAIN — see
[docs/troubleshooting.md](../../docs/troubleshooting.md)). It wraps official,
free, no-auth public health APIs so the prior-auth agents no longer depend on a
third-party hosted endpoint that can disappear.

| Path (server name) | Tools | Upstream API |
|------|-------|--------------|
| `/icd10/mcp` (icd10-codes) | `validate_code`, `lookup_code`, `search_codes`, `get_hierarchy` | [NLM Clinical Tables ICD-10-CM/PCS](https://clinicaltables.nlm.nih.gov) |
| `/clinical_trials/mcp` (clinical-trials) | `search_trials`, `get_trial_details` | [ClinicalTrials.gov API v2](https://clinicaltrials.gov/data-api/api) |
| `/npi/mcp` (npi-registry) | `npi_validate`, `npi_lookup`, `npi_search` | [CMS NPPES NPI Registry](https://npiregistry.cms.hhs.gov) |
| `/cms_coverage/mcp` (cms-coverage) | `search_national_coverage`, `search_local_coverage`, `get_coverage_document`, `get_contractors` | [CMS Coverage API](https://api.coverage.cms.gov/docs/swagger/index.html) (NCD/LCD/Article — real determinations) |

PubMed (`pubmed.mcp.claude.com`) was unaffected by the outage and stays as-is.

The tool **names and signatures match exactly what the agent `SKILL.md` files
call** (the same contract the DeepSense servers exposed), so the clinical and
coverage agents work unchanged. The per-domain path layout also mirrors
DeepSense, so the agents need **only their `MCP_*` URLs repointed** here.
Transport is **stateless + JSON** (no session to expire), which is exactly what
agent_framework's `MCPStreamableHTTPTool` speaks.

## Run & test locally

```bash
pip install -r requirements.txt
python server.py                         # serves on :8080
python test_client.py                    # MCP smoke test against all 4 domains
```

`test_client.py` connects over MCP Streamable HTTP (the same client stack the
agents use), lists tools, and exercises the contract tool names against the live
upstream APIs. Expect `9/9 tool calls returned live data`.

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
  (MCIM). `search_national_coverage` / `search_local_coverage` return ranked
  NCDs / LCDs + billing-coding Articles; `get_coverage_document` returns a
  policy's real ICD-10 covered/non-covered lists (Articles) and HCPCS list
  (LCDs/Articles) for Diagnosis-Policy Alignment; `get_contractors` lists the
  state's MACs. A free license token (AMA/ADA/AHA click-through, fetched
  automatically) is required for the CPT/HCPCS-bearing endpoints; NCD reports are
  public. **Policy discovery is by title keywords** (the API has no code→policy
  reverse search), so the coverage agent's multi-pass keyword strategy + patient
  `state` matter; ICD-10 medical-necessity lists live on billing/coding
  **Articles**, HCPCS lists on **LCDs** (esp. DME) — `search_local_coverage`
  returns both.
- **`billable` for ICD-10** is approximated as "exact match with no more-specific
  child code" (leaf node) from the NLM dataset.
- **Public ingress.** The Container App has external ingress so Foundry-hosted
  agents can reach it. It exposes only read-only public-government data and no
  secrets — the same posture as the DeepSense servers it replaces. Add
  authentication if you tighten that posture.
- **Resilience.** If this server is ever unreachable, the clinical/coverage
  agents now degrade to a valid HTTP 200 manual-review result instead of HTTP 500
  (startup reachability probe + handler fallback in each agent's `main.py`).
