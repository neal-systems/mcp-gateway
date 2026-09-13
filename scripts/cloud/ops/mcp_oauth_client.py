#!/usr/bin/env python3
"""Live OAuth evidence: log in to the deployed gateway as a real GitHub user
and make authorized tool calls.

Runs the FastMCP client's OAuth flow (dynamic client registration, PKCE,
browser consent) against https://<host>/mcp. This machine has no browser, so
the authorization URL is printed for the operator to open; the callback comes
back to a local port the operator's browser can reach. Prints a JSON summary:
tool names, outcome of each call, sizes. Never prints tokens, codes, or
headers.

Usage:
  mcp_oauth_client.py --url https://HOST/mcp [--callback-port 8765] [--expect-tools N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from fastmcp import Client
from fastmcp.client.auth import OAuth
from fastmcp.exceptions import ToolError


class PrintUrlOAuth(OAuth):
    """OAuth flow that prints the authorization URL instead of opening a browser."""

    async def redirect_handler(self, authorization_url: str) -> None:  # type: ignore[override]
        print("OPEN THIS URL IN YOUR BROWSER (signed in to GitHub):", file=sys.stderr)
        print(authorization_url, file=sys.stderr)
        sys.stderr.flush()


async def run(url: str, callback_port: int, expect_tools: int | None) -> int:
    auth = PrintUrlOAuth(
        mcp_url=url,
        client_name="mcp-gateway cloud evidence client",
        callback_port=callback_port,
        # 127.0.0.1, not localhost: a Windows browser resolves localhost to ::1
        # first while the callback server listens on IPv4.
        callback_host="127.0.0.1",
        callback_timeout=1800.0,
    )
    started = time.time()
    summary: dict = {"url": url, "checks": {}, "tools": [], "calls": {}}
    async with Client(url, auth=auth) as client:
        summary["checks"]["oauth_login"] = True
        summary["login_seconds"] = round(time.time() - started, 1)
        tools = await client.list_tools()
        summary["tools"] = sorted(t.name for t in tools)
        if expect_tools is not None:
            summary["checks"]["tool_count_expected"] = len(tools) == expect_tools

        async def call(name: str, **kwargs):
            try:
                result = await client.call_tool(name, kwargs)
                text = "".join(getattr(c, "text", "") for c in result.content)
                summary["calls"][name] = {"outcome": "ok", "result_chars": len(text)}
                return text
            except ToolError as exc:
                summary["calls"][name] = {"outcome": "denied_or_error", "error_prefix": str(exc)[:40]}
                return None

        listed = await call("list_services", status="healthy")
        summary["checks"]["list_services_ok"] = bool(listed) and "inventory-api" in listed
        searched = await call("search_runbooks", query="latency")
        summary["checks"]["search_runbooks_ok"] = bool(searched) and "latency" in searched.lower()
        details = await call("service_details", service_id="docs-site")
        summary["checks"]["service_details_ok"] = bool(details) and "degraded" in details

    ok = all(summary["checks"].values())
    summary["result"] = "pass" if ok else "fail"
    print(json.dumps(summary, sort_keys=True))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--callback-port", type=int, default=8765)
    parser.add_argument("--expect-tools", type=int, default=None)
    args = parser.parse_args()
    return asyncio.run(run(args.url, args.callback_port, args.expect_tools))


if __name__ == "__main__":
    sys.exit(main())
