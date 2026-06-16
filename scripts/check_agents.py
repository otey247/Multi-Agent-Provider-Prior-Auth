#!/usr/bin/env python3
"""Pre-flight health check for all Foundry Hosted Agents.

Verifies agent registration, App Insights connectivity, MCP tool connections,
backend health, and frontend availability. Run after deployment to confirm
everything is ready before submitting PA requests.

Usage:
    python scripts/check_agents.py              # full check once
    python scripts/check_agents.py --runtime    # include live hosted-agent smoke tests
    python scripts/check_agents.py --poll       # poll until all healthy
    python scripts/check_agents.py --version 6  # wait for specific version
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

AGENTS = [
    "clinical-reviewer-agent",
    "coverage-assessment-agent",
    "compliance-agent",
    "synthesis-agent",
]

MCP_CONNECTIONS = ["icd10", "pubmed", "clinical-trials", "npi-registry", "cms-coverage"]

RUNTIME_SMOKE_AGENTS = AGENTS

REQUIRED_AGENT_ENV = {
    "clinical-reviewer-agent": [
        "AZURE_AI_PROJECT_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
        "MCP_ICD10_CODES",
        "MCP_PUBMED",
        "MCP_CLINICAL_TRIALS",
    ],
    "coverage-assessment-agent": [
        "AZURE_AI_PROJECT_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
        "MCP_NPI_REGISTRY",
        "MCP_CMS_COVERAGE",
    ],
    "compliance-agent": [
        "AZURE_AI_PROJECT_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
    ],
    "synthesis-agent": [
        "AZURE_AI_PROJECT_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
    ],
}


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


def _clean_env_value(value):
    """Normalize values from azd env or shell exports."""
    return (value or "").strip().strip('"').strip().rstrip("/")


def _project_endpoint(account, project):
    """Return the hosted-agent-compatible Foundry project endpoint."""
    endpoint = (
        os.environ.get("AI_FOUNDRY_PROJECT_ENDPOINT")
        or os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
        or _get_azd_value("AI_FOUNDRY_PROJECT_ENDPOINT")
        or _get_azd_value("AZURE_AI_PROJECT_ENDPOINT")
    )
    endpoint = _clean_env_value(endpoint)

    if not endpoint and account and project:
        endpoint = f"https://{account}.services.ai.azure.com/api/projects/{project}"

    endpoint = endpoint.replace(
        ".cognitiveservices.azure.com",
        ".services.ai.azure.com",
    )
    return endpoint.rstrip("/")


def _get_ai_token():
    """Acquire an Azure AI token via Azure CLI."""
    try:
        result = subprocess.run(
            [
                "az",
                "account",
                "get-access-token",
                "--resource",
                "https://ai.azure.com",
                "-o",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return ""

    if result.returncode != 0:
        return ""

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ""

    return data.get("accessToken", "")


def _section(title):
    """Print a section header."""
    print(f"\n  {'='*50}")
    print(f"  {title}")
    print(f"  {'='*50}")


def _nested_get(data, *keys):
    """Read the first matching key from nested dictionaries."""
    current = data
    for key_options in keys:
        if not isinstance(current, dict):
            return None

        if isinstance(key_options, tuple):
            found = False
            for key in key_options:
                if key in current:
                    current = current[key]
                    found = True
                    break
            if not found:
                return None
        else:
            current = current.get(key_options)

    return current


def _agent_image(definition):
    """Extract the hosted agent container image from common SDK/CLI shapes."""
    image = _nested_get(
        definition,
        ("container_configuration", "containerConfiguration"),
        "image",
    )
    return image or definition.get("image") or "?"


def _agent_protocols(definition):
    """Extract registered container protocols from common SDK/CLI shapes."""
    records = (
        definition.get("container_protocol_versions")
        or definition.get("containerProtocolVersions")
        or definition.get("protocol_versions")
        or definition.get("protocolVersions")
        or []
    )
    if not isinstance(records, list):
        return "?"

    summaries = []
    for record in records:
        if not isinstance(record, dict):
            continue
        protocol = record.get("protocol") or record.get("name") or "?"
        version = record.get("version") or "?"
        summaries.append(f"{protocol}@{version}")

    return ", ".join(summaries) or "?"


def _status_fields(data):
    """Collect concise lifecycle/status fields from a CLI agent-show payload."""
    fields = []
    markers = ("status", "state", "provision", "deploy")

    def _walk(value, path):
        if len(fields) >= 10:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                key_lower = key.lower()
                if (
                    any(marker in key_lower for marker in markers)
                    and not isinstance(child, (dict, list))
                ):
                    fields.append((child_path, str(child)))
                elif isinstance(child, dict):
                    _walk(child, child_path)
                elif isinstance(child, list) and len(child) <= 4:
                    for idx, item in enumerate(child):
                        if isinstance(item, dict):
                            _walk(item, f"{child_path}[{idx}]")

    _walk(data, "")
    return fields


def _short_image(image):
    """Return a compact image name while preserving repository and tag."""
    if not image or image == "?":
        return "?"
    return image.split("/")[-1]


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
                missing_env = [
                    key for key in REQUIRED_AGENT_ENV.get(name, [])
                    if not env.get(key)
                ]
                image = _agent_image(defn)
                protocols = _agent_protocols(defn)
                status_fields = _status_fields(data)
                version_ok = not expected_version or str(version) == str(expected_version)
                results.append({
                    "name": name, "version": version, "status": "registered",
                    "has_ai_cs": has_ai_cs, "has_ai_cs_alt": has_ai_cs_alt,
                    "version_ok": version_ok, "missing_env": missing_env,
                    "image": image, "protocols": protocols,
                    "status_fields": status_fields,
                })
                if not version_ok or missing_env:
                    all_ok = False
            else:
                results.append({"name": name, "version": "?", "status": "not found",
                                "has_ai_cs": False, "has_ai_cs_alt": False,
                                "version_ok": False, "missing_env": [],
                                "image": "?", "protocols": "?",
                                "status_fields": []})
                all_ok = False
        except Exception:
            results.append({"name": name, "version": "?", "status": "error",
                            "has_ai_cs": False, "has_ai_cs_alt": False,
                            "version_ok": False, "missing_env": [],
                            "image": "?", "protocols": "?",
                            "status_fields": []})
            all_ok = False

    print(f"\n  {'Agent':<30} {'Version':>8}  {'AI CS':>6}  {'Env':>5}  {'Image':<28} {'Status':<12}")
    print(f"  {'-'*30} {'-'*8}  {'-'*6}  {'-'*5}  {'-'*28} {'-'*12}")
    for r in results:
        version = str(r["version"])
        cs_icon = "✓" if r["has_ai_cs"] else "✗"
        env_icon = "✓" if not r["missing_env"] else "✗"
        status_icon = "✓" if r["status"] == "registered" and r["version_ok"] else "✗"
        print(
            f"  {r['name']:<30} {'v' + version:>8}  {cs_icon:>6}  "
            f"{env_icon:>5}  {_short_image(r['image']):<28} {status_icon} {r['status']}"
        )
    print()

    print("  Registered protocols:")
    for r in results:
        print(f"  {r['name']:<30} {r['protocols']}")
    print()

    # Warnings
    for r in results:
        if r["status"] == "registered" and not r["has_ai_cs"]:
            print(f"  WARNING: {r['name']} missing APPLICATIONINSIGHTS_CONNECTION_STRING")
        if r["status"] == "registered" and not r["has_ai_cs_alt"]:
            print(f"  WARNING: {r['name']} missing APPLICATION_INSIGHTS_CONNECTION_STRING")
        if r["status"] == "registered" and r["missing_env"]:
            print(
                f"  ERROR: {r['name']} missing required runtime env: "
                f"{', '.join(r['missing_env'])}"
            )

    printed_status_header = False
    for r in results:
        if r["status_fields"]:
            if not printed_status_header:
                print()
                print("  Lifecycle/status fields:")
                printed_status_header = True
            summary = "; ".join(
                f"{key}={value[:80]}" for key, value in r["status_fields"]
            )
            print(f"  {r['name']:<30} {summary}")

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


def _runtime_smoke_payload(agent_name):
    """Return a minimal request payload for a live hosted-agent smoke test."""
    request_data = {
        "request_id": "runtime-smoke-check",
        "patient_name": "Runtime Smoke Test",
        "patient_dob": "1970-01-01",
        "provider_npi": "1912084401",
        "ordering_provider_npi": "1912084401",
        "ordering_provider_name": "Runtime Smoke Provider",
        "rendering_provider_specialty": "Internal Medicine",
        "insurance_id": "SMOKE-001",
        "diagnosis_codes": ["J44.9"],
        "procedure_codes": ["E1390"],
        "clinical_notes": (
            "Patient has chronic hypoxemia with documented oxygen saturation "
            "of 87 percent on room air and requires home oxygen evaluation."
        ),
        "prior_treatment_history": ["Inhaled bronchodilator therapy"],
    }

    if agent_name == "coverage-assessment-agent":
        return {
            "request": request_data,
            "clinical_findings": {
                "diagnosis_validation": [
                    {
                        "code": "J44.9",
                        "valid": True,
                        "billable": True,
                        "description": "Chronic obstructive pulmonary disease",
                    }
                ],
                "clinical_extraction": {
                    "chief_complaint": "Hypoxemia",
                    "history_of_present_illness": request_data["clinical_notes"],
                    "prior_treatments": ["Inhaled bronchodilator therapy"],
                    "severity_indicators": ["Oxygen saturation 87 percent"],
                    "diagnostic_findings": [],
                    "extraction_confidence": 80,
                },
                "clinical_summary": "Smoke-test clinical findings.",
                "literature_support": [],
                "clinical_trials": [],
                "tool_results": [],
            },
        }

    if agent_name == "synthesis-agent":
        compliance_result = {
            "agent_name": "Documentation Completeness Agent",
            "overall_status": "complete",
            "checklist": [
                {"item": "Patient demographics", "status": "complete", "detail": "Present"},
                {"item": "Provider NPI", "status": "complete", "detail": "Present"},
                {"item": "Clinical notes", "status": "complete", "detail": "Present"},
            ],
            "missing_items": [],
        }
        clinical_result = {
            "agent_name": "Clinical Evidence Retrieval Agent",
            "diagnosis_validation": [
                {
                    "code": "J44.9",
                    "valid": True,
                    "billable": True,
                    "description": "Chronic obstructive pulmonary disease",
                }
            ],
            "clinical_extraction": {
                "chief_complaint": "Hypoxemia",
                "history_of_present_illness": request_data["clinical_notes"],
                "prior_treatments": ["Inhaled bronchodilator therapy"],
                "severity_indicators": ["Oxygen saturation 87 percent"],
                "diagnostic_findings": [],
                "extraction_confidence": 80,
            },
            "clinical_summary": "Smoke-test clinical findings.",
            "tool_results": [],
        }
        coverage_result = {
            "agent_name": "Policy Matching Agent",
            "provider_verification": {
                "npi": "1912084401",
                "name": "Runtime Smoke Provider",
                "specialty": "Internal Medicine",
                "status": "VERIFIED",
            },
            "coverage_policies": [],
            "criteria_assessment": [
                {
                    "criterion": "Provider credential verification",
                    "status": "MET",
                    "confidence": 90,
                    "evidence": ["Smoke-test provider verified"],
                },
                {
                    "criterion": "Medical necessity fallback",
                    "status": "MET",
                    "confidence": 80,
                    "evidence": ["Oxygen saturation 87 percent"],
                },
            ],
            "documentation_gaps": [],
            "tool_results": [],
        }
        return {
            "request": request_data,
            "compliance_result": compliance_result,
            "clinical_result": clinical_result,
            "coverage_result": coverage_result,
            "cpt_validation": {
                "valid": True,
                "summary": "1/1 codes valid format",
                "results": [{"code": "E1390", "valid_format": True}],
            },
        }

    return request_data


def _invoke_runtime_smoke(agent_name, endpoint, token):
    """Invoke one Foundry hosted agent and return (ok, detail)."""
    url = (
        f"{endpoint}/agents/{agent_name}"
        f"/endpoint/protocols/openai/responses?api-version=v1"
    )
    body = {
        "input": json.dumps(_runtime_smoke_payload(agent_name)),
        "stream": False,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Foundry-Features": "HostedAgents=V1Preview",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if 200 <= resp.status < 300:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    return True, f"HTTP {resp.status} non-JSON response"

                if isinstance(payload, dict) and payload.get("error"):
                    return False, f"HTTP {resp.status}: error={str(payload['error'])[:500]}"

                status = payload.get("status") if isinstance(payload, dict) else None
                if status and status != "completed":
                    error = payload.get("error") if isinstance(payload, dict) else ""
                    return False, f"HTTP {resp.status}: status={status} error={str(error)[:500]}"

                return True, f"HTTP {resp.status} status={status or 'unknown'}"
            return False, f"HTTP {resp.status}: {raw[:500]}"
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        details = []
        for header in (
            "x-platform-error-source",
            "x-platform-error-detail",
            "x-agent-session-id",
        ):
            value = exc.headers.get(header)
            if value:
                details.append(f"{header}={value[:300]}")
        detail_text = f" ({'; '.join(details)})" if details else ""
        body = raw[:1000] if raw else "(empty response body)"
        return False, f"HTTP {exc.code}{detail_text}: {body}"
    except Exception as exc:
        return False, str(exc)


def check_runtime_smoke(account, project):
    """Live invoke hosted agents through Foundry Responses API."""
    _section("Hosted Agent Runtime Smoke Test")
    endpoint = _project_endpoint(account, project)
    if not endpoint:
        print("  SKIP: Could not determine Foundry project endpoint")
        return False

    token = _get_ai_token()
    if not token:
        print("  FAILED: Could not acquire Azure AI token via Azure CLI")
        return False

    all_ok = True
    for agent_name in RUNTIME_SMOKE_AGENTS:
        ok, detail = _invoke_runtime_smoke(agent_name, endpoint, token)
        icon = "✓" if ok else "✗"
        print(f"  {agent_name:<30} {icon} {detail}")
        if not ok:
            all_ok = False

    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Pre-flight health check for Foundry Hosted Agents")
    parser.add_argument("--poll", action="store_true", help="Poll until all agents are ready")
    parser.add_argument("--timeout", type=int, default=5, help="Max minutes to poll (default: 5)")
    parser.add_argument("--version", type=int, help="Expected version number to wait for")
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="Invoke all hosted agents to verify live runtime execution",
    )
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
    runtime_ok = check_runtime_smoke(account, project) if args.runtime else None

    # --- Summary ---
    _section("Summary")
    checks = [
        ("Agent Registration", agents_ok),
        ("App Insights Connection", insights_ok),
        ("MCP Tool Connections", mcp_ok),
        ("Backend Health", backend_ok),
        ("Frontend Available", frontend_ok),
    ]
    if runtime_ok is not None:
        checks.append(("Hosted Agent Runtime Smoke Test", runtime_ok))
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
        if args.runtime:
            print("  All checks passed. Ready to submit PA requests.")
        else:
            print("  Control-plane checks passed.")
            print("  Run with --runtime to verify live clinical/coverage agent execution.")
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
