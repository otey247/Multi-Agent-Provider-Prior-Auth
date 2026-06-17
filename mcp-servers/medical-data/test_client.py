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
    ("icd10", "validate_icd10", {"code": "J44.9"}),
    ("icd10", "lookup_icd10", {"query": "chronic hypoxemia", "max_results": 3}),
    ("clinical_trials", "search_clinical_trials", {"condition": "COPD", "max_results": 2}),
    ("npi", "lookup_npi", {"npi_number": "1912084401"}),
    ("cms_coverage", "search_coverage", {"keywords": "home oxygen", "codes": ["E1390"]}),
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
