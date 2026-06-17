"""Create the Foundry Toolboxes that back the clinical and coverage agents.

Hosted Foundry agents can reach the Foundry *project domain* (the model endpoint
compliance/synthesis already use) but NOT arbitrary public internet — so a hosted
agent cannot call the public ca-mcp medical-data server directly (the call hangs
and the request 500s). A Foundry Toolbox is a managed MCP endpoint *on the project
domain* that proxies out to the real MCP servers from Foundry's own network. The
agents connect to the toolbox (reachable); Foundry reaches ca-mcp (reachable).

This creates two toolboxes mirroring the per-agent tool split:
  clinical-tools : icd10, pubmed, clinical_trials
  coverage-tools : npi, cms_coverage

Tools are exposed to the model as ``{server_label}___{tool_name}``
(e.g. ``icd10___validate_code``).

Run from a checkout with the azd env loaded:
    eval "$(azd env get-values | sed 's/^/export /')"
    pip install -U azure-ai-projects azure-identity mcp
    python scripts/create_toolbox.py            # create/update + verify
    python scripts/create_toolbox.py --verify   # verify only

Docs: https://learn.microsoft.com/azure/foundry/agents/how-to/tools/toolbox
"""
import asyncio
import os
import sys

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

_TOKEN_SCOPE = "https://ai.azure.com/.default"
_PREVIEW_HEADER = {"Foundry-Features": "Toolboxes=V1Preview"}


def _project_endpoint() -> str:
    ep = os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT") or os.environ.get(
        "AZURE_AI_PROJECT_ENDPOINT"
    )
    if ep:
        return ep.rstrip("/")
    account = os.environ.get("AI_FOUNDRY_ACCOUNT_NAME")
    project = os.environ.get("AI_FOUNDRY_PROJECT_NAME")
    if not (account and project):
        sys.exit(
            "Set AI_FOUNDRY_PROJECT_ENDPOINT (or AI_FOUNDRY_ACCOUNT_NAME + "
            "AI_FOUNDRY_PROJECT_NAME). Run: eval \"$(azd env get-values | sed 's/^/export /')\""
        )
    return f"https://{account}.services.ai.azure.com/api/projects/{project}"


def _mcp(server_label: str, server_url: str) -> dict:
    # require_approval="never": all medical-data tools are read-only (search/
    # validate/lookup). See the agent-mcp-toolbox skill auth-and-approval ref.
    return {
        "type": "mcp",
        "server_label": server_label,
        "server_url": server_url,
        "require_approval": "never",
    }


def _toolboxes() -> dict:
    base = (os.environ.get("MEDICAL_MCP_BASE_URL", "") or "").strip().rstrip("/")
    if not base:
        sys.exit("MEDICAL_MCP_BASE_URL not set — deploy mcp-medical-data first.")
    pubmed = os.environ.get("MCP_PUBMED", "https://pubmed.mcp.claude.com/mcp")
    return {
        "clinical-tools": [
            _mcp("icd10", f"{base}/icd10/mcp"),
            _mcp("pubmed", pubmed),
            _mcp("clinical_trials", f"{base}/clinical_trials/mcp"),
        ],
        "coverage-tools": [
            _mcp("npi", f"{base}/npi/mcp"),
            _mcp("cms_coverage", f"{base}/cms_coverage/mcp"),
        ],
    }


# A representative read-only call per toolbox to prove the proxy reaches ca-mcp.
_SAMPLE = {
    "clinical-tools": ("icd10___validate_code", {"code": "J44.9", "code_type": "diagnosis"}),
    "coverage-tools": ("npi___npi_validate", {"npi": "1912084401"}),
}


def _create(project: AIProjectClient, name: str, tools: list) -> object:
    tb = getattr(getattr(project, "beta", None), "toolboxes", None)
    if tb is None:
        sys.exit(
            "azure-ai-projects has no .beta.toolboxes — run: "
            "pip install -U azure-ai-projects"
        )
    fn = getattr(tb, "create_version", None) or getattr(tb, "create_toolbox_version", None)
    if fn is None:
        sys.exit("toolboxes client has no create_version/create_toolbox_version method")
    try:
        return fn(name=name, description=f"prior-auth {name}", tools=tools)
    except TypeError:
        return fn(toolbox_name=name, description=f"prior-auth {name}", tools=tools)


async def _verify(endpoint: str, credential: DefaultAzureCredential, name: str) -> bool:
    token = credential.get_token(_TOKEN_SCOPE).token
    url = f"{endpoint}/toolboxes/{name}/mcp?api-version=v1"
    headers = {"Authorization": f"Bearer {token}", **_PREVIEW_HEADER}
    try:
        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = [t.name for t in (await session.list_tools()).tools]
                tool_name, args = _SAMPLE[name]
                result = await session.call_tool(tool_name, args)
                text = result.content[0].text if result.content else "{}"
                print(f"  [OK] {name}: {len(names)} tools; {tool_name} -> {text[:200]}")
                return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [ERR] {name}: {type(exc).__name__}: {str(exc)[:240]}")
        return False


def main() -> int:
    verify_only = "--verify" in sys.argv
    endpoint = _project_endpoint()
    credential = DefaultAzureCredential()
    project = AIProjectClient(endpoint=endpoint, credential=credential)

    ok = True
    for name, tools in _toolboxes().items():
        if not verify_only:
            version = _create(project, name, tools)
            print(f"[OK] toolbox '{name}' version {getattr(version, 'version', '?')}")
            print(f"     endpoint: {endpoint}/toolboxes/{name}/mcp?api-version=v1")
        ok = asyncio.run(_verify(endpoint, credential, name)) and ok

    if not ok:
        print("\nOne or more toolbox verifications failed.")
        return 1
    print("\nAll toolboxes verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
