#!/usr/bin/env python3
"""Curated, authorized, read-only FastMCP gateway.

The public example has no private backend. Every tool reads the bundled sample
JSON file, accepts a fixed input shape, and has no mutation path.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext

import scope
from scope import ConfigError

logger = logging.getLogger("mcp_gateway")
PLACEHOLDER = "<SET_BY_OPERATOR>"


def _credential(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("YOUR_"):
        return PLACEHOLDER
    return value


def load_config() -> dict:
    return {
        "base_url": os.environ.get("GATEWAY_BASE_URL", "https://gateway.example.com"),
        "github_client_id": _credential("GITHUB_CLIENT_ID"),
        "github_client_secret": _credential("GITHUB_CLIENT_SECRET"),
        "jwt_signing_key": _credential("GATEWAY_JWT_SIGNING_KEY"),
        "client_storage": os.environ.get(
            "GATEWAY_CLIENT_STORAGE", "/data/client_storage"
        ),
        "host": os.environ.get("GATEWAY_HOST", "127.0.0.1"),
        "port": int(os.environ.get("GATEWAY_PORT", "8080")),
        "sample_data": Path(
            os.environ.get(
                "GATEWAY_SAMPLE_DATA", str(Path(__file__).with_name("sample_data.json"))
            )
        ),
    }


def credentials_are_placeholders(cfg: dict) -> bool:
    return PLACEHOLDER in (
        cfg["github_client_id"],
        cfg["github_client_secret"],
        cfg["jwt_signing_key"],
    )


class AllowlistedGitHubProvider(GitHubProvider):
    """GitHub OAuth provider that rejects identities absent from the allowlist."""

    def __init__(self, *args, allowlist: dict[str, str], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if not allowlist:
            raise ConfigError("refusing to create an auth provider with an empty allowlist")
        self._allowlist = allowlist

    async def verify_token(self, token: str) -> AccessToken | None:
        access = await super().verify_token(token)
        if access is None:
            return None
        github_id = (access.claims or {}).get("sub")
        role = scope.role_for(github_id, self._allowlist)
        if role is None:
            logger.warning("GitHub identity denied by allowlist")
            return None
        logger.info("GitHub identity admitted with role %s", role)
        return access


def _current_role(allowlist: dict[str, str]) -> str | None:
    access = get_access_token()
    if access is None:
        return None
    return scope.role_for((access.claims or {}).get("sub"), allowlist)


class ScopeMiddleware(Middleware):
    """Filter visible tools and independently enforce every direct call."""

    def __init__(self, allowlist: dict[str, str]) -> None:
        self._allowlist = allowlist

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        tools = await call_next(context)
        role = _current_role(self._allowlist)
        return [tool for tool in tools if scope.is_tool_allowed(role, tool.name)]

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        role = _current_role(self._allowlist)
        name = getattr(context.message, "name", None)
        if not scope.is_tool_allowed(role, name or ""):
            raise ToolError(f"Unknown tool: {name}")
        return await call_next(context)


def _read_sample_data(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("sample data is missing or invalid") from exc
    if not isinstance(data.get("services"), list) or not isinstance(data.get("runbooks"), list):
        raise ConfigError("sample data must contain service and runbook lists")
    return data


def build_app(cfg: dict, allowlist: dict[str, str], with_auth: bool = True) -> FastMCP:
    if not allowlist:
        raise ConfigError("refusing to build a server with an empty allowlist")
    data = _read_sample_data(cfg["sample_data"])
    auth = _build_auth(cfg, allowlist) if with_auth else None
    mcp = FastMCP(name="read-only-mcp-gateway", auth=auth)
    mcp.add_middleware(ScopeMiddleware(allowlist))

    def role() -> str | None:
        return _current_role(allowlist)

    @mcp.tool
    def list_services(status: str | None = None) -> str:
        """List fictional sample services, optionally filtered by status."""
        if not scope.is_tool_allowed(role(), "list_services"):
            raise ToolError("Not authorized.")
        wanted = (status or "").strip().lower()
        services = [
            item
            for item in data["services"]
            if isinstance(item, dict) and (not wanted or item.get("status") == wanted)
        ]
        return json.dumps(services, indent=2)

    @mcp.tool
    def service_details(service_id: str) -> str:
        """Return one fictional sample service by its exact identifier."""
        if not scope.is_tool_allowed(role(), "service_details"):
            raise ToolError("Not authorized.")
        for item in data["services"]:
            if isinstance(item, dict) and item.get("id") == service_id:
                return json.dumps(item, indent=2)
        raise ToolError("No such sample service.")

    @mcp.tool
    def search_runbooks(query: str, limit: int = 5) -> str:
        """Search fictional sample runbooks by a case-insensitive phrase."""
        if not scope.is_tool_allowed(role(), "search_runbooks"):
            raise ToolError("Not authorized.")
        needle = (query or "").strip().lower()
        if not needle:
            raise ToolError("query must not be empty")
        limit = max(1, min(int(limit), 10))
        matches = [
            item
            for item in data["runbooks"]
            if isinstance(item, dict)
            and needle in f"{item.get('title', '')} {item.get('body', '')}".lower()
        ][:limit]
        return json.dumps(matches, indent=2)

    return mcp


def _build_auth(cfg: dict, allowlist: dict[str, str]) -> AllowlistedGitHubProvider:
    if credentials_are_placeholders(cfg):
        raise ConfigError("OAuth credentials or signing key are placeholders")

    from key_value.aio.stores.disk import DiskStore

    storage = Path(cfg["client_storage"])
    storage.mkdir(parents=True, exist_ok=True)
    client_store = DiskStore(directory=str(storage))

    signing_key = cfg["jwt_signing_key"]
    if signing_key == PLACEHOLDER:
        key_path = storage / "jwt_signing_key"
        if key_path.is_file():
            signing_key = key_path.read_text(encoding="utf-8").strip()
        if not signing_key:
            signing_key = secrets.token_hex(32)
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(signing_key)

    return AllowlistedGitHubProvider(
        allowlist=allowlist,
        **{
            "client_id": cfg["github_client_id"],
            "client_secret": cfg["github_client_secret"],
            "base_url": cfg["base_url"],
            "client_storage": client_store,
            "jwt_signing_key": signing_key,
            "timeout_seconds": 10,
        },
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    allowlist = scope.load_allowlist()
    app = build_app(cfg, allowlist, with_auth=True)
    app.run(transport="http", host=cfg["host"], port=cfg["port"])


if __name__ == "__main__":
    main()
