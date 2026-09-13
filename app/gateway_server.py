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
import warnings
from pathlib import Path

# Authlib registers an "always" filter for its own deprecation class when it is
# imported, so the ignore has to be installed after that and before FastMCP
# pulls the httpx shim in. It keeps the container log stream to JSON lines.
import authlib.deprecate  # noqa: E402

warnings.filterwarnings("ignore", category=authlib.deprecate.AuthlibDeprecationWarning)

from fastmcp import FastMCP  # noqa: E402
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from starlette.requests import Request
from starlette.responses import JSONResponse

import scope
import telemetry as telemetry_module
from scope import ConfigError

logger = logging.getLogger("mcp_gateway")
PLACEHOLDER = "<SET_BY_OPERATOR>"
MIN_SIGNING_KEY_LENGTH = 32
NO_STORE_HEADERS = {"Cache-Control": "no-store"}


def _credential(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("YOUR_"):
        return PLACEHOLDER
    return value


def load_config() -> dict:
    state_dir = os.environ.get("GATEWAY_STATE_DIR", "/data/state")
    explicit_client_storage = os.environ.get("GATEWAY_CLIENT_STORAGE")
    fault_inject = os.environ.get("GATEWAY_FAULT_INJECT", "").strip() or None
    return {
        "base_url": os.environ.get("GATEWAY_BASE_URL", "https://gateway.example.com"),
        "github_client_id": _credential("GITHUB_CLIENT_ID"),
        "github_client_secret": _credential("GITHUB_CLIENT_SECRET"),
        "jwt_signing_key": _credential("GATEWAY_JWT_SIGNING_KEY"),
        "state_dir": state_dir,
        "client_storage": (
            explicit_client_storage
            if explicit_client_storage
            else str(Path(state_dir) / "client_storage")
        ),
        "jwt_signing_key_file": os.environ.get(
            "GATEWAY_JWT_SIGNING_KEY_FILE", str(Path(state_dir) / "jwt_signing_key")
        ),
        "release_id": os.environ.get("GATEWAY_RELEASE_ID", "dev"),
        "fault_inject": fault_inject,
        "host": os.environ.get("GATEWAY_HOST", "127.0.0.1"),
        "port": int(os.environ.get("GATEWAY_PORT", "8080")),
        "sample_data": Path(
            os.environ.get(
                "GATEWAY_SAMPLE_DATA", str(Path(__file__).with_name("sample_data.json"))
            )
        ),
    }


def credentials_are_placeholders(cfg: dict) -> bool:
    """OAuth client id/secret fail closed. The signing key does NOT belong
    here: a placeholder signing key selects the generate-once path in
    `_resolve_signing_key`, it is never an error on its own."""
    return PLACEHOLDER in (cfg["github_client_id"], cfg["github_client_secret"])


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


def _ensure_dir_0700(path: Path) -> None:
    """Create the directory and tighten it to 0700. A bind mount owned by
    another uid cannot be chmod'ed from inside the container; that is a
    warning, not a startup failure, because readiness still requires the
    directory to be writable and the host owns its permissions."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except PermissionError:
        logger.warning(
            "could not set mode 0700 on state directory; it is owned by another user",
            extra={"state_dir_mode": oct(path.stat().st_mode & 0o777)},
        )


def _state_dir_writable(state_dir: Path) -> bool:
    probe = state_dir / f".writable-probe-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def build_app(
    cfg: dict,
    allowlist: dict[str, str],
    with_auth: bool = True,
    telemetry: telemetry_module.Telemetry | None = None,
) -> FastMCP:
    if not allowlist:
        raise ConfigError("refusing to build a server with an empty allowlist")
    data = _read_sample_data(cfg["sample_data"])

    state_dir = Path(cfg["state_dir"])
    _ensure_dir_0700(state_dir)

    signing_key_present = False
    if with_auth:
        auth, signing_key_present = _build_auth(cfg, allowlist)
    else:
        auth = None

    fault_injected = cfg.get("fault_inject") == "not_ready"

    mcp = FastMCP(name="read-only-mcp-gateway", auth=auth)
    if telemetry is not None:
        # Outermost first: the tool span must wrap the authorization check so
        # a denial is observed as a denial, not as a call that never happened.
        mcp.add_middleware(telemetry_module.ToolSpanMiddleware(telemetry))
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

    def readiness_checks() -> dict[str, bool]:
        return {
            "sample_data": isinstance(data.get("services"), list)
            and isinstance(data.get("runbooks"), list),
            "state_dir_writable": _state_dir_writable(state_dir),
            "signing_key": signing_key_present,
            "auth_configured": auth is not None,
            "fault_injected": fault_injected,
        }

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"}, headers=NO_STORE_HEADERS)

    @mcp.custom_route("/readyz", methods=["GET"], include_in_schema=False)
    async def readyz(request: Request) -> JSONResponse:
        checks = readiness_checks()
        ready = (
            checks["sample_data"]
            and checks["state_dir_writable"]
            and checks["signing_key"]
            and checks["auth_configured"]
            and not checks["fault_injected"]
        )
        body = {"status": "ready" if ready else "not_ready", "checks": checks}
        return JSONResponse(
            body, status_code=200 if ready else 503, headers=NO_STORE_HEADERS
        )

    mcp.readiness_checks = readiness_checks
    if telemetry is not None:
        telemetry.set_readiness_probe(readiness_checks)

    return mcp


def create_http_app(mcp: FastMCP, telemetry: telemetry_module.Telemetry | None = None):
    """Return the mounted Starlette ASGI app so tests can drive it with
    starlette.testclient.TestClient."""
    middleware = telemetry_module.asgi_middleware(telemetry) if telemetry is not None else None
    return mcp.http_app(middleware=middleware)


def _resolve_signing_key(key_file: Path, explicit_key: str) -> str:
    """Generate-once contract (CONTRACTS.md section 1). An explicit,
    non-placeholder key of sufficient length is used verbatim and no file is
    written. Otherwise: reuse an existing non-empty key file, or generate one
    and persist it atomically with mode 0600; never rotate on restart."""
    if explicit_key != PLACEHOLDER:
        if len(explicit_key) < MIN_SIGNING_KEY_LENGTH:
            raise ConfigError(
                f"GATEWAY_JWT_SIGNING_KEY must be at least {MIN_SIGNING_KEY_LENGTH} characters"
            )
        return explicit_key

    key_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(key_file.parent, 0o700)
    except OSError:
        pass

    existing = ""
    if key_file.is_file():
        existing = key_file.read_text(encoding="utf-8").strip()
    if existing:
        return existing

    generated = secrets.token_hex(32)
    tmp_path = key_file.with_name(f"{key_file.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(generated)
        os.replace(tmp_path, key_file)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    os.chmod(key_file, 0o600)
    return generated


def _build_auth(cfg: dict, allowlist: dict[str, str]) -> tuple[AllowlistedGitHubProvider, bool]:
    if credentials_are_placeholders(cfg):
        raise ConfigError("OAuth client id or secret are placeholders")

    from key_value.aio.stores.disk import DiskStore

    storage = Path(cfg["client_storage"])
    _ensure_dir_0700(storage)
    client_store = DiskStore(directory=str(storage))

    signing_key = _resolve_signing_key(Path(cfg["jwt_signing_key_file"]), cfg["jwt_signing_key"])

    provider = AllowlistedGitHubProvider(
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
    return provider, bool(signing_key)


def main() -> None:
    telemetry = telemetry_module.configure_from_env(os.environ)
    cfg = load_config()
    allowlist = scope.load_allowlist()
    app = build_app(cfg, allowlist, with_auth=True, telemetry=telemetry)
    logger.info("gateway starting", extra={"release_id": cfg["release_id"], "port": cfg["port"]})
    try:
        app.run(
            transport="http",
            host=cfg["host"],
            port=cfg["port"],
            show_banner=False,
            middleware=telemetry_module.asgi_middleware(telemetry),
            # uvicorn would otherwise install its own console handlers and
            # print outside the structured, redacted stream.
            uvicorn_config={"log_config": None},
        )
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    main()
