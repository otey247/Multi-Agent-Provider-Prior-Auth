#!/usr/bin/env python3
"""Pre-flight health check for all Foundry Hosted Agents.

Verifies agent registration, App Insights connectivity, MCP tool connections,
backend health, and frontend availability. Run after deployment to confirm
everything is ready before submitting PA requests.

Usage:
    python scripts/check_agents.py              # full check once
    python scripts/check_agents.py --poll       # poll until all healthy
    python scripts/check_agents.py --version 6  # wait for specific version
"""

import argparse
import json
import os
import subprocess
import sys
import time

AGENTS = [
    "clinical-reviewer-agent",
    "coverage-assessment-agent",
    "compliance-agent",
    "synthesis-agent",
]

MCP_CONNECTIONS = ["icd10", "pubmed", "clinical-trials", "npi-registry", "cms-coverage"]


def _get_azd_value(key):
    """Get a value from azd env, returns empty string on failure."""
    try:
        result = subprocess.run(
            ["azd", "env", "get-value", key],
            capture_output=True, text=True, timeout=10,
        )
        val = result.stdout.strip()
        return val if val and "ERROR" not in val else ""
    except Exception:
        return ""


def _section(title):
    """Print a section header."""
    print(f"\n  {'='*50}")
    print(f"  {title}")
    print(f"  {'='*50}")


def check_agents(account, project, expected_version=None):
    """Check agent registration and version."""
    _section("Agent Registration")
    results = []
    all_ok = True
    for name in AGENTS:
        try:
            result = subprocess.run(
                ["az", "cognitiveservices", "agent", "show",
                 "--account-name", account, "--project-name", project,
                 "--name", name, "-o", "json"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                latest = data.get("versions", {}).get("latest", {})
                version = latest.get("version", "?")
                defn = latest.get("definition", {})
                env = defn.get("environment_variables", {})
                has_ai_cs = bool(env.get("APPLICATIONINSIGHTS_CONNECTION_STRING"))
                has_ai_cs_alt = bool(env.get("APPLICATION_INSIGHTS_CONNECTION_STRING"))
                version_ok = not expected_version or str(version) == str(expected_version)
                results.append({
                    "name": name, "version": version, "status": "registered",
                    "has_ai_cs": has_ai_cs, "has_ai_cs_alt": has_ai_cs_alt,
                    "version_ok": version_ok,
                })
                if not version_ok:
                    all_ok = False
            else:
                results.append({"name": name, "version": "?", "status": "not found",
                                "has_ai_cs": False, "has_ai_cs_alt": False, "version_ok": False})
                all_ok = False
        except Exception:
            results.append({"name": name, "version": "?", "status": "error",
                            "has_ai_cs": False, "has_ai_cs_alt": False, "version_ok": False})
            all_ok = False

    print(f"\n  {'Agent':<30} {'Version':>8}  {'AI CS':>6}  {'Status':<12}")
    print(f"  {'-'*30} {'-'*8}  {'-'*6}  {'-'*12}")
    for r in results:
        version = str(r["version"])
        cs_icon = "✓" if r["has_ai_cs"] else "✗"
        status_icon = "✓" if r["status"] == "registered" and r["version_ok"] else "✗"
        print(f"  {r['name']:<30} {'v' + version:>8}  {cs_icon:>6}  {status_icon} {r['status']}")
    print()

    # Warnings
    for r in results:
        if r["status"] == "registered" and not r["has_ai_cs"]:
            print(f"  WARNING: {r['name']} missing APPLICATIONINSIGHTS_CONNECTION_STRING")
        if r["status"] == "registered" and not r["has_ai_cs_alt"]:
            print(f"  WARNING: {r['name']} missing APPLICATION_INSIGHTS_CONNECTION_STRING")

    return all_ok, results


def check_app_insights():
    """Check App Insights connection string availability."""
    _section("Application Insights")
    cs = _get_azd_value("APPLICATION_INSIGHTS_CONNECTION_STRING")
    if cs:
        # Extract key parts
        parts = dict(p.split("=", 1) for p in cs.split(";") if "=" in p)
        ikey = parts.get("InstrumentationKey", "?")[:12] + "..."
        endpoint = parts.get("IngestionEndpoint", "?")
        print(f"  Connection string: SET (ikey={ikey})")
        print(f"  Ingestion endpoint: {endpoint}")
        return True
    else:
        print("  Connection string: NOT SET")
        print("  Agent observability will be disabled.")
        return False


def check_mcp_connections(account, project, subscription, resource_group):
    """Check Foundry MCP tool connections exist."""
    _section("MCP Tool Connections")
    if not subscription or not resource_group:
        print("  SKIP: AZURE_SUBSCRIPTION_ID or AZURE_RESOURCE_GROUP not set")
        return True

    try:
        result = subprocess.run(
            ["az", "rest", "--method", "GET",
             "--url", f"https://management.azure.com/subscriptions/{subscription}"
                      f"/resourceGroups/{resource_group}/providers/Microsoft.CognitiveServices"
                      f"/accounts/{account}/projects/{project}/connections"
                      f"?api-version=2025-10-01-preview"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            connections = {c["name"]: c["properties"].get("category", "?")
                          for c in data.get("value", [])}
            all_ok = True
            for mcp in MCP_CONNECTIONS:
                if mcp in connections:
                    print(f"  {mcp:<20} ✓ {connections[mcp]}")
                else:
                    print(f"  {mcp:<20} ✗ NOT FOUND")
                    all_ok = False

            # Check App Insights connection
            if "app-insights" in connections:
                print(f"  {'app-insights':<20} ✓ {connections['app-insights']}")
            else:
                print(f"  {'app-insights':<20} ✗ NOT FOUND (Foundry Traces will not work)")
                all_ok = False
            return all_ok
    except Exception as e:
        print(f"  ERROR: {e}")
    return False


def check_backend():
    """Check backend Container App health endpoint."""
    _section("Backend Health")
    backend_url = _get_azd_value("backendUrl")
    if not backend_url:
        print("  SKIP: backendUrl not set in azd env")
        return True

    if not backend_url.startswith("http"):
        backend_url = f"https://{backend_url}"

    try:
        import urllib.request
        req = urllib.request.Request(f"{backend_url}/health", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            print(f"  {backend_url}/health -> {status} OK")
            return True
    except Exception as e:
        print(f"  {backend_url}/health -> FAILED ({e})")
        return False


def check_frontend():
    """Check frontend Container App availability."""
    _section("Frontend")
    frontend_url = _get_azd_value("frontendUrl")
    if not frontend_url:
        print("  SKIP: frontendUrl not set in azd env")
        return True

    if not frontend_url.startswith("http"):
        frontend_url = f"https://{frontend_url}"

    try:
        import urllib.request
        req = urllib.request.Request(frontend_url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"  {frontend_url} -> {resp.status} OK")
            return True
    except Exception as e:
        print(f"  {frontend_url} -> FAILED ({e})")
        return False


def main():
    parser = argparse.ArgumentParser(description="Pre-flight health check for Foundry Hosted Agents")
    parser.add_argument("--poll", action="store_true", help="Poll until all agents are ready")
    parser.add_argument("--timeout", type=int, default=5, help="Max minutes to poll (default: 5)")
    parser.add_argument("--version", type=int, help="Expected version number to wait for")
    args = parser.parse_args()

    account = os.environ.get("AI_FOUNDRY_ACCOUNT_NAME") or _get_azd_value("AI_FOUNDRY_ACCOUNT_NAME")
    project = os.environ.get("AI_FOUNDRY_PROJECT_NAME") or _get_azd_value("AI_FOUNDRY_PROJECT_NAME")
    subscription = os.environ.get("AZURE_SUBSCRIPTION_ID") or _get_azd_value("AZURE_SUBSCRIPTION_ID")
    resource_group = os.environ.get("AZURE_RESOURCE_GROUP") or _get_azd_value("AZURE_RESOURCE_GROUP")

    if not account or not project:
        print("ERROR: AI_FOUNDRY_ACCOUNT_NAME and AI_FOUNDRY_PROJECT_NAME must be set.", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Pre-flight check: {project}")

    # --- Run all checks ---
    agents_ok, agent_results = check_agents(account, project, args.version)
    insights_ok = check_app_insights()
    mcp_ok = check_mcp_connections(account, project, subscription, resource_group)
    backend_ok = check_backend()
    frontend_ok = check_frontend()

    # --- Summary ---
    _section("Summary")
    checks = [
        ("Agent Registration", agents_ok),
        ("App Insights Connection", insights_ok),
        ("MCP Tool Connections", mcp_ok),
        ("Backend Health", backend_ok),
        ("Frontend Available", frontend_ok),
    ]
    all_ok = True
    for name, ok in checks:
        icon = "✓" if ok else "✗"
        print(f"  {icon} {name}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        frontend_url = _get_azd_value("frontendUrl")
        if frontend_url and not frontend_url.startswith("http"):
            frontend_url = f"https://{frontend_url}"
        print("  All checks passed. Ready to submit PA requests.")
        if frontend_url:
            print(f"  Frontend: {frontend_url}")
    else:
        print("  Some checks failed. Review the output above.")

    # --- Poll mode for agents ---
    if args.poll and not agents_ok:
        print("\n  Polling for agent readiness...")
        deadline = time.time() + args.timeout * 60
        while time.time() < deadline:
            time.sleep(15)
            agents_ok, agent_results = check_agents(account, project, args.version)
            if agents_ok:
                print("  All agents ready.")
                sys.exit(0)
        print(f"  Timeout after {args.timeout} minutes.")
        sys.exit(1)

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
