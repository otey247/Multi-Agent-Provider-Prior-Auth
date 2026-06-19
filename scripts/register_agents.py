#!/usr/bin/env python3
"""Register and start the 4 prior-auth agents as Foundry Hosted Agents.

This script is called by the azure.yaml postprovision hook after agent images
have been built and pushed to ACR. It:

1. Creates Foundry MCP tool connections (idempotent) for all 5 MCP servers
   so hosted agents can call external tools via Foundry's managed proxy.
2. Registers each agent with Foundry Agent Service (creating a new version).
3. Starts the agent deployments.

Requirements:
  pip install "azure-ai-projects>=2.0.0"

Required environment variables (set automatically by the postprovision hook):
  AI_FOUNDRY_PROJECT_ENDPOINT        — Preferred Foundry project endpoint
  AZURE_AI_PROJECT_ENDPOINT          — Legacy fallback Foundry project endpoint
  AZURE_CONTAINER_REGISTRY_ENDPOINT  — ACR login server (e.g. myacr.azurecr.io)
  AI_FOUNDRY_ACCOUNT_NAME            — Foundry account name
  AI_FOUNDRY_PROJECT_NAME            — Foundry project name
  AZURE_OPENAI_DEPLOYMENT_NAME       — Model deployment name (default: gpt-5.4)
  AZURE_SUBSCRIPTION_ID              — Azure subscription ID
  AZURE_RESOURCE_GROUP               — Resource group name

Optional environment variables:
  APPLICATION_INSIGHTS_CONNECTION_STRING — If present, passed to Hosted Agents as
                                           MONITORING_CONNECTION_STRING (the reserved
                                           APPLICATION*INSIGHTS names are rejected in the
                                           registration payload; each agent's main.py
                                           bridges it to APPLICATIONINSIGHTS_CONNECTION_STRING
                                           at startup so the agentserver exports telemetry).
  IMAGE_TAG                             — ACR image tag (default: latest)
  HOSTED_AGENT_RESPONSES_PROTOCOL_VERSION — Responses container protocol version
                                            (default: v0.1.1)
"""

import os
import subprocess
import sys
import time


# ---------------------------------------------------------------------------
# Hosted Agent environment variable safety
# ---------------------------------------------------------------------------
# Azure Foundry Hosted Agents reserve several environment variable names for
# platform use. Passing these in HostedAgentDefinition.environment_variables
# causes registration to fail with invalid_payload. Keep this sanitizer even
# if the agent env dictionaries below are already clean, so future additions
# do not reintroduce the issue.
# ---------------------------------------------------------------------------
RESERVED_ENV_EXACT = {
    "APPLICATIONINSIGHTS_CONNECTION_STRING",
    "APPLICATION_INSIGHTS_CONNECTION_STRING",
}

RESERVED_ENV_PREFIXES = (
    "FOUNDRY_",
    "AGENT_",
)


def sanitize_hosted_agent_env(env: dict[str, str]) -> dict[str, str]:
    """Remove env vars that Foundry Hosted Agents reserve for platform use."""
    sanitized: dict[str, str] = {}

    for key, value in env.items():
        if key in RESERVED_ENV_EXACT or key.startswith(RESERVED_ENV_PREFIXES):
            print(f"    [skip reserved env] {key}")
            continue

        sanitized[key] = value

    return sanitized


def _clean_env_value(value: str) -> str:
    """Normalize values coming from azd env or shell exports."""
    return (value or "").strip().strip('"').strip().rstrip("/")


def _normalize_project_endpoint(
    endpoint: str,
    account_name: str,
    project_name: str,
) -> str:
    """Return the hosted-agent-compatible Foundry project endpoint.

    AI_FOUNDRY_PROJECT_ENDPOINT is the preferred variable name. Some older
    infrastructure outputs still use the cognitiveservices host, but hosted
    agent runtime calls require the services.ai.azure.com project endpoint.
    """
    normalized = _clean_env_value(endpoint)

    if not normalized and account_name and project_name:
        normalized = (
            f"https://{account_name}.services.ai.azure.com"
            f"/api/projects/{project_name}"
        )

    normalized = normalized.replace(
        ".cognitiveservices.azure.com",
        ".services.ai.azure.com",
    )

    return normalized.rstrip("/")


def _run_agent_start_command(
    account_name: str,
    project_name: str,
    agent_name: str,
    version_num: str,
    attempts: int = 3,
) -> tuple[bool, str]:
    """Start a hosted agent via the (preview) CLI.

    Only ``--agent-version`` is valid for this command (the older ``--version``
    and no-version forms are arg errors, so they are not tried). A freshly
    created version can briefly 404 ("Operation returned an invalid status
    'Not Found'") before it becomes startable, so retry a few times with a
    short backoff. Auto-start is best-effort: scale-to-zero agents also
    cold-start on the first request, so a persistent failure is non-fatal.
    """
    cmd = [
        "az", "cognitiveservices", "agent", "start",
        "--account-name", account_name,
        "--project-name", project_name,
        "--name", agent_name,
        "--agent-version", version_num,
    ]

    last = ""
    for attempt in range(1, attempts + 1):
        result = subprocess.run(cmd, capture_output=True, text=True)
        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        if result.returncode == 0 or "already exists with status Running" in output:
            return True, output

        last = f"exit={result.returncode}: {output or '(no output)'}"
        # A just-created version often 404s until it is startable — retry.
        if attempt < attempts and "Not Found" in output:
            time.sleep(8)
            continue
        break

    return False, last


def _build_hosted_agent_definition(
    *,
    HostedAgentDefinition,
    ProtocolVersionRecord,
    AgentProtocol,
    ContainerConfiguration,
    agent_def: dict,
):
    """Build a HostedAgentDefinition across beta SDK model shapes.

    azure-ai-projects 2.x exposes image-based hosted agents with direct
    ``image`` and ``container_protocol_versions`` fields. Earlier preview
    builds used ``container_configuration`` and ``protocol_versions``. Keep
    both paths so deployment is not pinned to one transient beta spelling.
    """
    protocol_records = [
        ProtocolVersionRecord(
            protocol=AgentProtocol.RESPONSES,
            version=os.environ.get(
                "HOSTED_AGENT_RESPONSES_PROTOCOL_VERSION",
                # Platform requires "1.0.0"; "v0.1.1" is stored but rejected at
                # runtime ("Unsupported responses protocol version ''"). Must match
                # the default printed at the responses_protocol_version read site.
                "1.0.0",
            ),
        )
    ]
    common = {
        "cpu": agent_def["cpu"],
        "memory": agent_def["memory"],
        "environment_variables": agent_def["safe_env"],
        "tools": agent_def["tools"],
    }

    annotations = getattr(HostedAgentDefinition, "__annotations__", {}) or {}
    if "container_protocol_versions" in annotations or "image" in annotations:
        return HostedAgentDefinition(
            container_protocol_versions=protocol_records,
            image=agent_def["image"],
            **common,
        )

    if ContainerConfiguration is None:
        raise RuntimeError(
            "Installed azure-ai-projects HostedAgentDefinition expects "
            "ContainerConfiguration, but that model is not available."
        )

    return HostedAgentDefinition(
        protocol_versions=protocol_records,
        container_configuration=ContainerConfiguration(
            image=agent_def["image"],
        ),
        **common,
    )


# ---------------------------------------------------------------------------
# MCP tool connection definitions
# ---------------------------------------------------------------------------
# Each entry defines a Foundry project connection for a remote MCP server.
# These connections are created via the ARM REST API and appear in the Foundry
# portal under Build > Tools as configured MCP tools.
#
# DeepSense servers require a custom User-Agent header (without it they return
# a 301 redirect). PubMed (Anthropic) works without authentication.
# ---------------------------------------------------------------------------
# Self-hosted medical-data MCP server base URL (replaces the retired
# mcp.deepsense.ai host, now NXDOMAIN). azd injects MEDICAL_MCP_BASE_URL from
# the mcp-medical-data container app FQDN (see infra/main.bicep). When unset,
# the legacy DeepSense URLs are used as a fallback — but that host is dead, so
# set MEDICAL_MCP_BASE_URL. PubMed (Anthropic) is unaffected and stays as-is.
_MEDICAL_MCP_BASE = (os.environ.get("MEDICAL_MCP_BASE_URL", "") or "").strip().rstrip("/")


def _medical_mcp_url(path: str, legacy: str) -> str:
    """Self-hosted MCP URL for a domain path, or the legacy DeepSense URL."""
    return f"{_MEDICAL_MCP_BASE}/{path}/mcp" if _MEDICAL_MCP_BASE else legacy


MCP_CONNECTIONS = [
    {
        "name": "icd10",
        "url": _medical_mcp_url("icd10", "https://mcp.deepsense.ai/icd10_codes/mcp"),
        "auth": "None",
        "keys": {},
    },
    {
        "name": "pubmed",
        "url": "https://pubmed.mcp.claude.com/mcp",
        "auth": "None",
        "keys": {},
    },
    {
        "name": "clinical-trials",
        "url": _medical_mcp_url("clinical_trials", "https://mcp.deepsense.ai/clinical_trials/mcp"),
        "auth": "None",
        "keys": {},
    },
    {
        "name": "npi-registry",
        "url": _medical_mcp_url("npi", "https://mcp.deepsense.ai/npi_registry/mcp"),
        "auth": "None",
        "keys": {},
    },
    {
        "name": "cms-coverage",
        "url": _medical_mcp_url("cms_coverage", "https://mcp.deepsense.ai/cms_coverage/mcp"),
        "auth": "None",
        "keys": {},
    },
]


def _create_mcp_connections(
    subscription_id: str,
    resource_group: str,
    account_name: str,
    project_name: str,
) -> None:
    """Create Foundry MCP tool connections via the ARM REST API.

    Each connection registers a remote MCP server in the Foundry project so
    hosted agents can call MCP tools through Foundry's managed proxy instead
    of making direct outbound HTTP calls from the container. This solves
    IP-based rate-limiting issues with external MCP servers (e.g.
    pubmed.mcp.claude.com blocks requests from Foundry container egress IPs).

    Uses PUT (idempotent) -- safe to call on every deploy without conflicts.
    Connections appear in the Foundry portal under Build > Tools.
    """
    import httpx
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    token = credential.get_token("https://management.azure.com/.default").token
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    api_version = "2025-06-01"  # GA API version for Foundry project connections
    base_url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices"
        f"/accounts/{account_name}/projects/{project_name}"
    )

    print("  Creating Foundry MCP tool connections...")
    for mcp in MCP_CONNECTIONS:
        url = f"{base_url}/connections/{mcp['name']}?api-version={api_version}"

        body: dict = {
            "properties": {
                "category": "RemoteTool",
                "target": mcp["url"],
                "authType": mcp["auth"],
                "metadata": {"type": "custom_MCP"},
            }
        }

        if mcp["auth"] == "CustomKeys" and mcp["keys"]:
            body["properties"]["credentials"] = {"keys": mcp["keys"]}

        try:
            resp = httpx.put(url, json=body, headers=headers, timeout=15)
            if resp.status_code in (200, 201):
                print(f"    [OK] {mcp['name']}")
            else:
                print(f"    [!!] {mcp['name']}: HTTP {resp.status_code}")
        except Exception as exc:
            print(f"    [!!] {mcp['name']}: {exc}")


def run() -> None:
    account_name = _clean_env_value(os.environ.get("AI_FOUNDRY_ACCOUNT_NAME", ""))
    project_name = _clean_env_value(os.environ.get("AI_FOUNDRY_PROJECT_NAME", ""))

    project_endpoint = _normalize_project_endpoint(
        os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT", "")
        or os.environ.get("AZURE_AI_PROJECT_ENDPOINT", ""),
        account_name,
        project_name,
    )

    acr_endpoint = _clean_env_value(os.environ.get("AZURE_CONTAINER_REGISTRY_ENDPOINT", ""))
    model_name = _clean_env_value(os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.4"))
    app_insights_cs = os.environ.get("APPLICATION_INSIGHTS_CONNECTION_STRING", "")
    image_tag = _clean_env_value(os.environ.get("IMAGE_TAG", "latest"))
    responses_protocol_version = os.environ.get(
        "HOSTED_AGENT_RESPONSES_PROTOCOL_VERSION",
        "1.0.0",  # platform requires 1.0.0; v0.1.1 is rejected ("Unsupported responses protocol version")
    )
    subscription_id = _clean_env_value(os.environ.get("AZURE_SUBSCRIPTION_ID", ""))
    resource_group = _clean_env_value(os.environ.get("AZURE_RESOURCE_GROUP", ""))

    if image_tag == "latest":
        print("  WARNING: IMAGE_TAG=latest — Foundry may not re-pull updated images.")
        print("  For reliable deploys, set IMAGE_TAG to a unique value:")
        print("    export IMAGE_TAG=$(date -u +%Y%m%d%H%M%S)")

    if app_insights_cs:
        print(
            f"  App Insights: connection string available (len={len(app_insights_cs)}); "
            f"passing via MONITORING_CONNECTION_STRING (the reserved APPLICATION*INSIGHTS "
            f"names are rejected by the platform; each agent bridges it at startup)"
        )
    else:
        print(
            "  App Insights: connection string not set in hook environment. "
            "Hosted Agent telemetry will be disabled."
        )

    if not project_endpoint:
        print(
            "ERROR: AI_FOUNDRY_PROJECT_ENDPOINT is not set and could not be derived "
            "from AI_FOUNDRY_ACCOUNT_NAME + AI_FOUNDRY_PROJECT_NAME.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"  Foundry project endpoint: {project_endpoint}")
    print(f"  Hosted Responses protocol: responses@{responses_protocol_version}")
    if not acr_endpoint:
        print("ERROR: AZURE_CONTAINER_REGISTRY_ENDPOINT is not set.", file=sys.stderr)
        sys.exit(1)
    if not account_name or not project_name:
        print(
            "ERROR: AI_FOUNDRY_ACCOUNT_NAME and AI_FOUNDRY_PROJECT_NAME must be set.",
            file=sys.stderr,
        )
        sys.exit(1)

    acr_name = acr_endpoint.replace(".azurecr.io", "")
    agent_images = ["agent-clinical", "agent-coverage", "agent-compliance", "agent-synthesis"]
    missing_images = []
    for img in agent_images:
        result = subprocess.run(
            [
                "az",
                "acr",
                "repository",
                "show-tags",
                "--name",
                acr_name,
                "--repository",
                img,
                "--query",
                f"[?@=='{image_tag}']",
                "-o",
                "tsv",
            ],
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            missing_images.append(f"{img}:{image_tag}")

    if missing_images:
        print(
            f"ERROR: The following images are missing from ACR ({acr_name}):\n"
            + "\n".join(f"  - {img}" for img in missing_images)
            + "\n\nBuild them first with:\n"
            + "\n".join(
                f"  az acr build --registry {acr_name} --image {img.split(':')[0]}:{image_tag} "
                f"--platform linux/amd64 ./agents/{img.split(':')[0].replace('agent-', '')}"
                for img in missing_images
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from azure.ai.projects import AIProjectClient
        from azure.ai.projects.models import (
            AgentProtocol,
            HostedAgentDefinition,
            ProtocolVersionRecord,
        )
        from azure.core.pipeline.policies import CustomHookPolicy
        from azure.identity import DefaultAzureCredential
        try:
            from azure.ai.projects.models import ContainerConfiguration
        except ImportError:
            ContainerConfiguration = None
    except ImportError:
        print(
            "ERROR: azure-ai-projects is not installed. Run:\n"
            "  pip install 'azure-ai-projects>=2.0.0'",
            file=sys.stderr,
        )
        sys.exit(1)

    if subscription_id and resource_group:
        _create_mcp_connections(subscription_id, resource_group, account_name, project_name)
    else:
        print(
            "  WARN: AZURE_SUBSCRIPTION_ID or AZURE_RESOURCE_GROUP not set -- "
            "skipping MCP connection creation"
        )

    class _FoundryPreviewPolicy(CustomHookPolicy):
        """Injects the Foundry preview feature header into every request."""

        def on_request(self, request):
            request.http_request.headers["Foundry-Features"] = "HostedAgents=V1Preview"

    client = AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True,
        per_call_policies=[_FoundryPreviewPolicy()],
    )

    mcp_icd10 = _medical_mcp_url("icd10", "https://mcp.deepsense.ai/icd10_codes/mcp")
    mcp_pubmed = "https://pubmed.mcp.claude.com/mcp"
    mcp_trials = _medical_mcp_url("clinical_trials", "https://mcp.deepsense.ai/clinical_trials/mcp")
    mcp_npi = _medical_mcp_url("npi", "https://mcp.deepsense.ai/npi_registry/mcp")
    mcp_cms = _medical_mcp_url("cms_coverage", "https://mcp.deepsense.ai/cms_coverage/mcp")

    # Foundry Toolbox endpoints. Hosted agents can reach the Foundry project domain
    # but NOT arbitrary public internet, so the clinical/coverage agents consume the
    # medical-data MCP servers through these toolboxes (created by
    # scripts/create_toolbox.py) which Foundry proxies out to the real servers. The
    # MCP_* values above are what the toolboxes themselves point at.
    toolbox_clinical = f"{project_endpoint}/toolboxes/clinical-tools/mcp?api-version=v1"
    toolbox_coverage = f"{project_endpoint}/toolboxes/coverage-tools/mcp?api-version=v1"
    if not _MEDICAL_MCP_BASE:
        print(
            "  WARN: MEDICAL_MCP_BASE_URL not set — ICD10/trials/NPI/CMS MCP tools "
            "point at the retired mcp.deepsense.ai host and will be unreachable. "
            "Deploy mcp-medical-data and set MEDICAL_MCP_BASE_URL."
        )

    agents = [
        {
            "name": "clinical-reviewer-agent",
            "description": (
                "Validates ICD-10 diagnosis codes, extracts clinical indicators with "
                "confidence scoring, searches PubMed literature and ClinicalTrials.gov, "
                "and returns a structured clinical profile for downstream coverage assessment."
            ),
            "image": f"{acr_endpoint}/agent-clinical:{image_tag}",
            "cpu": "1",
            "memory": "2Gi",
            "env": {
                "AI_FOUNDRY_PROJECT_ENDPOINT": project_endpoint,
                "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
                "MONITORING_CONNECTION_STRING": app_insights_cs,
                "AZURE_OPENAI_DEPLOYMENT_NAME": model_name,
                "TOOLBOX_ENDPOINT": toolbox_clinical,
                "MCP_ICD10_CODES": mcp_icd10,
                "MCP_PUBMED": mcp_pubmed,
                "MCP_CLINICAL_TRIALS": mcp_trials,
            },
            "tools": [],
        },
        {
            "name": "coverage-assessment-agent",
            "description": (
                "Verifies provider NPI credentials, searches Medicare NCDs/LCDs via CMS "
                "Coverage MCP, maps clinical findings to policy criteria with "
                "MET/NOT_MET/INSUFFICIENT assessment, and produces documentation gap analysis."
            ),
            "image": f"{acr_endpoint}/agent-coverage:{image_tag}",
            "cpu": "1",
            "memory": "2Gi",
            "env": {
                "AI_FOUNDRY_PROJECT_ENDPOINT": project_endpoint,
                "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
                "MONITORING_CONNECTION_STRING": app_insights_cs,
                "AZURE_OPENAI_DEPLOYMENT_NAME": model_name,
                "TOOLBOX_ENDPOINT": toolbox_coverage,
                "MCP_NPI_REGISTRY": mcp_npi,
                "MCP_CMS_COVERAGE": mcp_cms,
            },
            "tools": [],
        },
        {
            "name": "compliance-agent",
            "description": (
                "Validates documentation completeness for prior authorization requests "
                "using a 10-item checklist covering patient information, provider NPI, "
                "insurance details, medical codes, clinical notes quality, NCCI bundling "
                "risk, and service type classification. Uses no external tools — pure LLM reasoning."
            ),
            "image": f"{acr_endpoint}/agent-compliance:{image_tag}",
            "cpu": "0.5",
            "memory": "1Gi",
            "env": {
                "AI_FOUNDRY_PROJECT_ENDPOINT": project_endpoint,
                "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
                "MONITORING_CONNECTION_STRING": app_insights_cs,
                "AZURE_OPENAI_DEPLOYMENT_NAME": os.environ.get(
                    "AZURE_OPENAI_COMPLIANCE_DEPLOYMENT_NAME", "gpt-5.4"
                ),
            },
            "tools": [],
        },
        {
            "name": "synthesis-agent",
            "description": (
                "Synthesizes outputs from Compliance, Clinical Reviewer, and Coverage agents "
                "into a final APPROVE or PEND recommendation using 3-gate evaluation "
                "(Provider → Codes → Medical Necessity), weighted confidence scoring, "
                "and a structured audit trail."
            ),
            "image": f"{acr_endpoint}/agent-synthesis:{image_tag}",
            "cpu": "1",
            "memory": "2Gi",
            "env": {
                "AI_FOUNDRY_PROJECT_ENDPOINT": project_endpoint,
                "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
                "MONITORING_CONNECTION_STRING": app_insights_cs,
                "AZURE_OPENAI_DEPLOYMENT_NAME": model_name,
            },
            "tools": [],
        },
    ]

    print()
    for agent_def in agents:
        name = agent_def["name"]
        print(f"  Registering {name}...", end="", flush=True)

        try:
            safe_env = sanitize_hosted_agent_env(agent_def["env"])
            agent_def["safe_env"] = safe_env

            agent_version = client.agents.create_version(
                agent_name=name,
                description=agent_def["description"],
                definition=_build_hosted_agent_definition(
                    HostedAgentDefinition=HostedAgentDefinition,
                    ProtocolVersionRecord=ProtocolVersionRecord,
                    AgentProtocol=AgentProtocol,
                    ContainerConfiguration=ContainerConfiguration,
                    agent_def=agent_def,
                ),
            )

            version_num = agent_version.version
            print(f" version {version_num} created")

        except Exception as exc:
            print(f" FAILED\nERROR: {exc}", file=sys.stderr)
            sys.exit(1)

        # Route 100% of endpoint traffic to the version just created. This is
        # the RELIABLE activation path. The previous approach
        # (`az cognitiveservices agent start`) is a preview CLI extension that
        # is frequently unavailable in CI and locally; when it fails, the
        # endpoint keeps serving an OLDER version, so new images/code deploy
        # but behavior never changes (observed as persistent runtime errors on
        # an endpoint pinned to a stale version). patch_agent_details uses the
        # base data-plane SDK and works without the CLI extension.
        print(f"  Routing {name} traffic to version {version_num}...", end="", flush=True)
        try:
            client.beta.agents.patch_agent_details(
                name,
                {
                    "agent_endpoint": {
                        "version_selector": {
                            "version_selection_rules": [
                                {
                                    "type": "FixedRatio",
                                    "agent_version": str(version_num),
                                    "traffic_percentage": 100,
                                }
                            ]
                        }
                    }
                },
            )
            print(" routed")
        except Exception as exc:  # noqa: BLE001 — best effort, non-fatal
            print(
                f" WARNING: could not set version routing via SDK ({exc}).\n"
                f"  Route traffic from the Foundry portal: Agents > {name} > "
                f"send 100% traffic to version {version_num}."
            )

    print()
    print("  All 4 agents registered successfully.")
    print(
        "  Note: if auto-start failed, start each agent from the Foundry portal:\n"
        "  Microsoft Foundry portal > your project > Agents > select agent > Start"
    )


if __name__ == "__main__":
    run()
