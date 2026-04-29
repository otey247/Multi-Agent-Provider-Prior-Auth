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
  AZURE_AI_PROJECT_ENDPOINT          — Foundry project endpoint
  AZURE_CONTAINER_REGISTRY_ENDPOINT  — ACR login server (e.g. myacr.azurecr.io)
  AI_FOUNDRY_ACCOUNT_NAME            — Foundry account name
  AI_FOUNDRY_PROJECT_NAME            — Foundry project name
  AZURE_OPENAI_DEPLOYMENT_NAME       — Model deployment name (default: gpt-5.4)
  AZURE_SUBSCRIPTION_ID              — Azure subscription ID
  AZURE_RESOURCE_GROUP               — Resource group name

Optional environment variables:
  APPLICATION_INSIGHTS_CONNECTION_STRING — For agent observability (passed to agents)
  IMAGE_TAG                             — ACR image tag (default: latest)
"""

import os
import subprocess
import sys


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

        # Build the connection body matching the Foundry portal format:
        # category=RemoteTool + metadata.type=custom_MCP makes it visible
        # in the portal's Tools page as a configured MCP tool.
        body: dict = {
            "properties": {
                "category": "RemoteTool",
                "target": mcp["url"],
                "authType": mcp["auth"],
                "metadata": {"type": "custom_MCP"},
            }
        }

        # Add credentials only for Key-based auth (DeepSense servers need
        # the User-Agent header; PubMed works unauthenticated)
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
    project_endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "").rstrip("/")
    acr_endpoint = os.environ.get("AZURE_CONTAINER_REGISTRY_ENDPOINT", "").rstrip("/")
    account_name = os.environ.get("AI_FOUNDRY_ACCOUNT_NAME", "")
    project_name = os.environ.get("AI_FOUNDRY_PROJECT_NAME", "")
    model_name = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.4")
    app_insights_cs = os.environ.get("APPLICATION_INSIGHTS_CONNECTION_STRING", "")
    image_tag = os.environ.get("IMAGE_TAG", "latest")
    subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    resource_group = os.environ.get("AZURE_RESOURCE_GROUP", "")

    # When using 'latest' tag, Foundry may not re-pull the image for an existing
    # version because the image reference hasn't changed. The azd up hook always
    # sets IMAGE_TAG to a timestamp (YYYYMMDDHHmmss) which avoids this issue.
    # For manual runs without IMAGE_TAG set, warn the user.
    if image_tag == "latest":
        print("  WARNING: IMAGE_TAG=latest — Foundry may not re-pull updated images.")
        print("  For reliable deploys, set IMAGE_TAG to a unique value:")
        print("    export IMAGE_TAG=$(date -u +%Y%m%d%H%M%S)")

    if app_insights_cs:
        print(f"  App Insights: connection string set (len={len(app_insights_cs)})")
    else:
        print("  App Insights: CONNECTION STRING NOT SET — agent observability will be disabled")

    if not project_endpoint:
        print("ERROR: AZURE_AI_PROJECT_ENDPOINT is not set.", file=sys.stderr)
        sys.exit(1)
    if not acr_endpoint:
        print("ERROR: AZURE_CONTAINER_REGISTRY_ENDPOINT is not set.", file=sys.stderr)
        sys.exit(1)
    if not account_name or not project_name:
        print(
            "ERROR: AI_FOUNDRY_ACCOUNT_NAME and AI_FOUNDRY_PROJECT_NAME must be set.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate that all agent images exist in ACR before registering
    acr_name = acr_endpoint.replace(".azurecr.io", "")
    agent_images = ["agent-clinical", "agent-coverage", "agent-compliance", "agent-synthesis"]
    missing_images = []
    for img in agent_images:
        result = subprocess.run(
            ["az", "acr", "repository", "show-tags", "--name", acr_name,
             "--repository", img, "--query", f"[?@=='{image_tag}']", "-o", "tsv"],
            capture_output=True, text=True,
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
    except ImportError:
        print(
            "ERROR: azure-ai-projects is not installed. Run:\n"
            "  pip install 'azure-ai-projects>=2.0.0'",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Step 1: Create Foundry MCP tool connections (idempotent) ---
    if subscription_id and resource_group:
        _create_mcp_connections(subscription_id, resource_group, account_name, project_name)
    else:
        print(
            "  WARN: AZURE_SUBSCRIPTION_ID or AZURE_RESOURCE_GROUP not set -- "
            "skipping MCP connection creation"
        )

    # --- Step 2: Register agents ---
    # The HostedAgents=V1Preview feature flag is required to attach MCPTool
    # definitions to HostedAgentDefinition (tools on hosted agents is preview).
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

    # MCP URLs passed to agent containers (agents wire MCPStreamableHTTPTool internally)
    mcp_icd10 = "https://mcp.deepsense.ai/icd10_codes/mcp"
    mcp_pubmed = "https://pubmed.mcp.claude.com/mcp"
    mcp_trials = "https://mcp.deepsense.ai/clinical_trials/mcp"
    mcp_npi = "https://mcp.deepsense.ai/npi_registry/mcp"
    mcp_cms = "https://mcp.deepsense.ai/cms_coverage/mcp"

    # Foundry MCPTool definitions are DISABLED — passing MCPTool definitions in
    # HostedAgentDefinition.tools causes the agentserver adapter to inject a
    # UserInfoContextMiddleware that calls /agents/{name}/tools/resolve. This API
    # is not yet available in all Foundry regions (returns 404), which crashes
    # the ASGI pipeline with "No response returned". MCP tools are handled
    # directly by MCPStreamableHTTPTool in each agent's main.py instead.
    # Re-enable when the tools/resolve API is GA in your Foundry region.
    # clinical_tools = [
    #     MCPTool(server_label="icd10", ...),
    #     MCPTool(server_label="pubmed", ...),
    #     MCPTool(server_label="clinical-trials", ...),
    # ]
    # coverage_tools = [
    #     MCPTool(server_label="npi-registry", ...),
    #     MCPTool(server_label="cms-coverage", ...),
    # ]

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
                "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
                "AZURE_OPENAI_DEPLOYMENT_NAME": model_name,
                "MCP_ICD10_CODES": mcp_icd10,
                "MCP_PUBMED": mcp_pubmed,
                "MCP_CLINICAL_TRIALS": mcp_trials,
                "APPLICATION_INSIGHTS_CONNECTION_STRING": app_insights_cs,
                "APPLICATIONINSIGHTS_CONNECTION_STRING": app_insights_cs,
            },
            "tools": [],  # MCPTool defs disabled (see comment above)
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
                "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
                "AZURE_OPENAI_DEPLOYMENT_NAME": model_name,
                "MCP_NPI_REGISTRY": mcp_npi,
                "MCP_CMS_COVERAGE": mcp_cms,
                "APPLICATION_INSIGHTS_CONNECTION_STRING": app_insights_cs,
                "APPLICATIONINSIGHTS_CONNECTION_STRING": app_insights_cs,
            },
            "tools": [],  # MCPTool defs disabled (see comment above)
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
                "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
                "AZURE_OPENAI_DEPLOYMENT_NAME": os.environ.get(
                    "AZURE_OPENAI_COMPLIANCE_DEPLOYMENT_NAME", "gpt-5.4"
                ),
                "APPLICATION_INSIGHTS_CONNECTION_STRING": app_insights_cs,
                "APPLICATIONINSIGHTS_CONNECTION_STRING": app_insights_cs,
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
                "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
                "AZURE_OPENAI_DEPLOYMENT_NAME": model_name,
                "APPLICATION_INSIGHTS_CONNECTION_STRING": app_insights_cs,
                "APPLICATIONINSIGHTS_CONNECTION_STRING": app_insights_cs,
            },
            "tools": [],
        },
    ]

    print()
    for agent_def in agents:
        name = agent_def["name"]
        print(f"  Registering {name}...", end="", flush=True)
        try:
            agent_version = client.agents.create_version(
                agent_name=name,
                description=agent_def["description"],
                definition=HostedAgentDefinition(
                    container_protocol_versions=[
                        ProtocolVersionRecord(
                            protocol=AgentProtocol.RESPONSES, version="v1"
                        )
                    ],
                    cpu=agent_def["cpu"],
                    memory=agent_def["memory"],
                    image=agent_def["image"],
                    environment_variables=agent_def["env"],
                    tools=agent_def["tools"],
                ),
            )
            version_num = agent_version.version
            print(f" version {version_num} created")
        except Exception as exc:
            print(f" FAILED\nERROR: {exc}", file=sys.stderr)
            sys.exit(1)

        # Start the new deployment via az CLI
        print(f"  Starting {name} (version {version_num})...", end="", flush=True)
        try:
            result = subprocess.run(
                [
                    "az", "cognitiveservices", "agent", "start",
                    "--account-name", account_name,
                    "--project-name", project_name,
                    "--name", name,
                    "--agent-version", str(version_num),
                ],
                check=True, capture_output=True, text=True,
            )
            print(" started")
        except subprocess.CalledProcessError as exc:
            if "already exists with status Running" in (exc.stderr or ""):
                print(" already running")
            else:
                print(
                    f" WARNING: could not auto-start via CLI ({exc.returncode}).\n"
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
