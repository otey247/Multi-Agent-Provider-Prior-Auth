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
  APPLICATION_INSIGHTS_CONNECTION_STRING — Logged by this script if present; not passed
                                           to Hosted Agents because the platform
                                           reserves App Insights env names.
  IMAGE_TAG                             — ACR image tag (default: latest)
"""

import os
import subprocess
import sys


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
) -> tuple[bool, str]:
    """Start a hosted agent, trying known CLI parameter variants.

    The Foundry Hosted Agent CLI surface is preview and has changed between
    extension versions. Keep the variants narrow, and return stderr so the
    deployment log preserves the real Azure CLI failure instead of only exit
    code 3.
    """
    base_cmd = [
        "az",
        "cognitiveservices",
        "agent",
        "start",
        "--account-name",
        account_name,
        "--project-name",
        project_name,
        "--name",
        agent_name,
    ]
    variants = [
        base_cmd + ["--agent-version", version_num],
        base_cmd + ["--version", version_num],
        base_cmd,
    ]

    failures: list[str] = []
    for cmd in variants:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )

        if result.returncode == 0:
            return True, output

        if "already exists with status Running" in output:
            return True, output

        failures.append(
            f"$ {' '.join(cmd)}\n"
            f"exit={result.returncode}\n"
            f"{output or '(no output)'}"
        )

    return False, "\n\n".join(failures)


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
MCP_CONNECTIONS = [
    {
        "name": "icd10",
        "url": "https://mcp.deepsense.ai/icd10_codes/mcp",
        "auth": "CustomKeys",
        "keys": {"User-Agent": "claude-code/1.0"},
    },
    {
        "name": "pubmed",
        "url": "https://pubmed.mcp.claude.com/mcp",
        "auth": "None",
        "keys": {},
    },
    {
        "name": "clinical-trials",
        "url": "https://mcp.deepsense.ai/clinical_trials/mcp",
        "auth": "CustomKeys",
        "keys": {"User-Agent": "claude-code/1.0"},
    },
    {
        "name": "npi-registry",
        "url": "https://mcp.deepsense.ai/npi_registry/mcp",
        "auth": "CustomKeys",
        "keys": {"User-Agent": "claude-code/1.0"},
    },
    {
        "name": "cms-coverage",
        "url": "https://mcp.deepsense.ai/cms_coverage/mcp",
        "auth": "CustomKeys",
        "keys": {"User-Agent": "claude-code/1.0"},
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
    subscription_id = _clean_env_value(os.environ.get("AZURE_SUBSCRIPTION_ID", ""))
    resource_group = _clean_env_value(os.environ.get("AZURE_RESOURCE_GROUP", ""))

    if image_tag == "latest":
        print("  WARNING: IMAGE_TAG=latest — Foundry may not re-pull updated images.")
        print("  For reliable deploys, set IMAGE_TAG to a unique value:")
        print("    export IMAGE_TAG=$(date -u +%Y%m%d%H%M%S)")

    if app_insights_cs:
        print(
            f"  App Insights: connection string available to deployment hook "
            f"(len={len(app_insights_cs)}); not passing it to Hosted Agents"
        )
    else:
        print(
            "  App Insights: connection string not set in hook environment. "
            "Hosted Agent platform telemetry env vars will still be injected if configured."
        )

    if not project_endpoint:
        print(
            "ERROR: AI_FOUNDRY_PROJECT_ENDPOINT is not set and could not be derived "
            "from AI_FOUNDRY_ACCOUNT_NAME + AI_FOUNDRY_PROJECT_NAME.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"  Foundry project endpoint: {project_endpoint}")
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
            ContainerConfiguration,
            HostedAgentDefinition,
            ProtocolVersionRecord,
        )
        from azure.core.pipeline.policies import CustomHookPolicy
        from azure.identity import DefaultAzureCredential
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

    mcp_icd10 = "https://mcp.deepsense.ai/icd10_codes/mcp"
    mcp_pubmed = "https://pubmed.mcp.claude.com/mcp"
    mcp_trials = "https://mcp.deepsense.ai/clinical_trials/mcp"
    mcp_npi = "https://mcp.deepsense.ai/npi_registry/mcp"
    mcp_cms = "https://mcp.deepsense.ai/cms_coverage/mcp"

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
                "AZURE_OPENAI_DEPLOYMENT_NAME": model_name,
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
                "AZURE_OPENAI_DEPLOYMENT_NAME": model_name,
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

            agent_version = client.agents.create_version(
                agent_name=name,
                description=agent_def["description"],
                definition=HostedAgentDefinition(
                    protocol_versions=[
                        ProtocolVersionRecord(
                            protocol=AgentProtocol.RESPONSES,
                            version="1.0.0",
                        )
                    ],
                    cpu=agent_def["cpu"],
                    memory=agent_def["memory"],
                    container_configuration=ContainerConfiguration(
                        image=agent_def["image"],
                    ),
                    environment_variables=safe_env,
                    tools=agent_def["tools"],
                ),
            )

            version_num = agent_version.version
            print(f" version {version_num} created")

        except Exception as exc:
            print(f" FAILED\nERROR: {exc}", file=sys.stderr)
            sys.exit(1)

        print(f"  Starting {name} (version {version_num})...", end="", flush=True)
        try:
            started, detail = _run_agent_start_command(
                account_name,
                project_name,
                name,
                str(version_num),
            )
            if started:
                print(" started")
                if detail:
                    print(f"    {detail}")
            else:
                print(
                    " WARNING: could not auto-start via CLI.\n"
                    f"{detail}\n"
                    f"  Manually start from Foundry portal: Agents > {name} > Start",
                )
        except FileNotFoundError:
            print(
                " WARNING: 'az' CLI not found -- start the agent from Foundry portal:\n"
                f"  Agents > {name} > Start"
            )

    print()
    print("  All 4 agents registered successfully.")
    print(
        "  Note: if auto-start failed, start each agent from the Foundry portal:\n"
        "  Microsoft Foundry portal > your project > Agents > select agent > Start"
    )


if __name__ == "__main__":
    run()
