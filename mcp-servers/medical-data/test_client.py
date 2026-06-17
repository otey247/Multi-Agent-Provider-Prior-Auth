"""Smoke test for the medical-data MCP server.

Connects over MCP Streamable HTTP (the same transport agent_framework's
MCPStreamableHTTPTool uses), lists tools, and calls one tool per domain
against the live upstream APIs.

Usage:  python test_client.py [base_url]   (default http://127.0.0.1:8080)
"""
import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080").rstrip("/")

CALLS = [
    ("icd10", "validate_code", {"code": "J44.1", "code_type": "diagnosis"}),
    ("icd10", "get_hierarchy", {"code_prefix": "J44"}),
    ("clinical_trials", "search_trials", {"query": "COPD", "limit": 2}),
    ("npi", "npi_validate", {"npi": "1912084401"}),
    ("npi", "npi_lookup", {"npi": "1912084401"}),
    ("cms_coverage", "search_national_coverage", {"keyword": "home oxygen"}),
    ("cms_coverage", "search_local_coverage", {"keyword": "oxygen", "state": "TX"}),
    ("cms_coverage", "get_contractors", {"state": "TX"}),
    ("cms_coverage", "get_coverage_document", {"document_id": "L33797", "document_type": "LCD"}),
]


async def _exercise(domain: str, tool: str, args: dict) -> bool:
    url = f"{BASE}/{domain}/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            assert tool in names, f"{tool} not advertised by {domain}: {names}"
            result = await session.call_tool(tool, args)
            text = result.content[0].text if result.content else "{}"
            payload = json.loads(text)
            err = payload.get("error")
            ok = not err
            print(f"[{'OK ' if ok else 'ERR'}] {domain}/{tool}  tools={names}")
            print("       " + json.dumps(payload)[:300])
            return ok


async def main() -> int:
    print(f"Testing {BASE}\n")
    results = [await _exercise(d, t, a) for d, t, a in CALLS]
    passed, total = sum(results), len(results)
    print(f"\n{passed}/{total} tool calls returned live data")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
