from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest

import gateway_server as server
import scope

OPERATOR_ID = "111111111"
VIEWER_ID = "222222222"
UNKNOWN_ID = "999999999"
ALLOWLIST = {
    OPERATOR_ID: scope.ROLE_OPERATOR,
    VIEWER_ID: scope.ROLE_VIEWER,
}


def config(tmp_path: Path) -> dict:
    state_dir = tmp_path / "state"
    return {
        "base_url": "https://gateway.example.com",
        "github_client_id": "obviously-fake-client-id",
        "github_client_secret": "obviously-fake-client-secret",
        "jwt_signing_key": "test-signing-value-not-for-use-but-32-chars",
        "state_dir": str(state_dir),
        "client_storage": str(tmp_path / "oauth"),
        "jwt_signing_key_file": str(state_dir / "jwt_signing_key"),
        "release_id": "dev",
        "fault_inject": None,
        "host": "127.0.0.1",
        "port": 8080,
        "sample_data": Path(__file__).parents[1] / "app" / "sample_data.json",
    }


def access(github_id: str | None):
    claims = {} if github_id is None else {"sub": github_id, "login": "example-user"}
    return server.AccessToken(
        token="inert-test-value", client_id="test-client", scopes=["user"], claims=claims
    )


def provider(monkeypatch, upstream_result, tmp_path):
    from key_value.aio.stores.disk import DiskStore

    async def fake_verify(self, token):
        return upstream_result

    monkeypatch.setattr(server.GitHubProvider, "verify_token", fake_verify)
    return server.AllowlistedGitHubProvider(
        allowlist=ALLOWLIST,
        **{
            "client_id": "obviously-fake-client-id",
            "client_secret": "obviously-fake-client-secret",
            "base_url": "https://gateway.example.com",
            "client_storage": DiskStore(directory=str(tmp_path / "provider-state")),
        },
    )


def test_allowlisted_identity_is_admitted(monkeypatch, tmp_path):
    auth = provider(monkeypatch, access(OPERATOR_ID), tmp_path)
    assert asyncio.run(auth.verify_token("inert-test-value")) is not None


def test_valid_but_unlisted_identity_is_denied(monkeypatch, tmp_path):
    auth = provider(monkeypatch, access(UNKNOWN_ID), tmp_path)
    assert asyncio.run(auth.verify_token("inert-test-value")) is None


def test_missing_identity_claim_is_denied(monkeypatch, tmp_path):
    auth = provider(monkeypatch, access(None), tmp_path)
    assert asyncio.run(auth.verify_token("inert-test-value")) is None


def test_upstream_rejection_stays_rejected(monkeypatch, tmp_path):
    auth = provider(monkeypatch, None, tmp_path)
    assert asyncio.run(auth.verify_token("inert-test-value")) is None


def test_provider_rejects_empty_allowlist(tmp_path):
    from key_value.aio.stores.disk import DiskStore

    with pytest.raises(scope.ConfigError):
        server.AllowlistedGitHubProvider(
            allowlist={},
            **{
                "client_id": "obviously-fake-client-id",
                "client_secret": "obviously-fake-client-secret",
                "base_url": "https://gateway.example.com",
                "client_storage": DiskStore(directory=str(tmp_path / "empty-state")),
            },
        )


def middleware(monkeypatch, role):
    instance = server.ScopeMiddleware(ALLOWLIST)
    monkeypatch.setattr(server, "_current_role", lambda _allowlist: role)
    return instance


def test_viewer_sees_only_its_catalog(monkeypatch):
    instance = middleware(monkeypatch, scope.ROLE_VIEWER)
    tools = [
        types.SimpleNamespace(name=name)
        for name in ["list_services", "service_details", "search_runbooks"]
    ]

    async def call_next(_context):
        return tools

    result = asyncio.run(instance.on_list_tools(types.SimpleNamespace(), call_next))
    assert {tool.name for tool in result} == {"list_services", "service_details"}


def test_direct_call_is_checked_independently(monkeypatch):
    instance = middleware(monkeypatch, scope.ROLE_VIEWER)
    body_ran = False

    async def call_next(_context):
        nonlocal body_ran
        body_ran = True
        return "unexpected"

    context = types.SimpleNamespace(message=types.SimpleNamespace(name="search_runbooks"))
    with pytest.raises(server.ToolError):
        asyncio.run(instance.on_call_tool(context, call_next))
    assert body_ran is False


def test_permitted_direct_call_runs(monkeypatch):
    instance = middleware(monkeypatch, scope.ROLE_VIEWER)

    async def call_next(_context):
        return "ok"

    context = types.SimpleNamespace(message=types.SimpleNamespace(name="list_services"))
    assert asyncio.run(instance.on_call_tool(context, call_next)) == "ok"


def test_sample_data_is_local_and_fictional(tmp_path):
    data = server._read_sample_data(config(tmp_path)["sample_data"])
    assert "Fictional sample data" in data["about"]
    assert len(data["services"]) == 3
    assert len(data["runbooks"]) == 3


def test_invalid_sample_data_stops_build(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    cfg = config(tmp_path)
    cfg["sample_data"] = invalid
    with pytest.raises(scope.ConfigError):
        server.build_app(cfg, ALLOWLIST, with_auth=False)


def test_placeholder_credentials_stop_authenticated_build(tmp_path):
    cfg = config(tmp_path)
    cfg["github_client_secret"] = server.PLACEHOLDER
    with pytest.raises(scope.ConfigError):
        server.build_app(cfg, ALLOWLIST, with_auth=True)


def test_all_three_example_tools_are_registered(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_current_role", lambda _allowlist: scope.ROLE_OPERATOR)
    app = server.build_app(config(tmp_path), ALLOWLIST, with_auth=False)
    tools = asyncio.run(app.list_tools())
    names = {getattr(tool, "name", tool) for tool in tools}
    assert names == {"list_services", "service_details", "search_runbooks"}


def test_example_tools_read_shipped_data(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_current_role", lambda _allowlist: scope.ROLE_OPERATOR)
    app = server.build_app(config(tmp_path), ALLOWLIST, with_auth=False)
    tools = {tool.name: tool for tool in asyncio.run(app.list_tools())}

    listed = tools["list_services"].fn(status="healthy")
    details = tools["service_details"].fn(service_id="docs-site")
    searched = tools["search_runbooks"].fn(query="latency")

    assert len(json.loads(listed)) == 1
    assert json.loads(details)["status"] == "degraded"
    assert json.loads(searched)[0]["title"] == "Investigating elevated latency"


def test_load_config_derives_state_paths_from_state_dir(monkeypatch, tmp_path):
    state_dir = tmp_path / "custom-state"
    monkeypatch.setenv("GATEWAY_STATE_DIR", str(state_dir))
    monkeypatch.delenv("GATEWAY_CLIENT_STORAGE", raising=False)
    monkeypatch.delenv("GATEWAY_JWT_SIGNING_KEY_FILE", raising=False)
    monkeypatch.delenv("GATEWAY_RELEASE_ID", raising=False)
    monkeypatch.delenv("GATEWAY_FAULT_INJECT", raising=False)

    cfg = server.load_config()

    assert cfg["state_dir"] == str(state_dir)
    assert cfg["client_storage"] == str(state_dir / "client_storage")
    assert cfg["jwt_signing_key_file"] == str(state_dir / "jwt_signing_key")
    assert cfg["release_id"] == "dev"
    assert cfg["fault_inject"] is None


def test_load_config_respects_explicit_client_storage_override(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    explicit_storage = tmp_path / "elsewhere"
    monkeypatch.setenv("GATEWAY_STATE_DIR", str(state_dir))
    monkeypatch.setenv("GATEWAY_CLIENT_STORAGE", str(explicit_storage))

    cfg = server.load_config()

    assert cfg["client_storage"] == str(explicit_storage)


def test_load_config_defaults_match_container_contract(monkeypatch):
    for name in (
        "GATEWAY_STATE_DIR",
        "GATEWAY_CLIENT_STORAGE",
        "GATEWAY_JWT_SIGNING_KEY_FILE",
        "GATEWAY_RELEASE_ID",
        "GATEWAY_FAULT_INJECT",
    ):
        monkeypatch.delenv(name, raising=False)

    cfg = server.load_config()

    assert cfg["state_dir"] == "/data/state"
    assert cfg["client_storage"] == "/data/state/client_storage"
    assert cfg["jwt_signing_key_file"] == "/data/state/jwt_signing_key"
    assert cfg["release_id"] == "dev"
    assert cfg["fault_inject"] is None


def test_telemetry_wraps_requests_and_denied_tool_calls(monkeypatch, tmp_path):
    """A denied direct call must appear as a 'denied' tool span, and every HTTP
    response must carry the correlation id, when telemetry is wired in."""
    import telemetry as telemetry_module
    from starlette.testclient import TestClient

    tel = telemetry_module.configure_telemetry(exporter="memory", release_id="test-rel")
    monkeypatch.setattr(server, "_current_role", lambda _allowlist: scope.ROLE_VIEWER)
    app = server.build_app(config(tmp_path), ALLOWLIST, with_auth=False, telemetry=tel)

    with TestClient(server.create_http_app(app, tel)) as client:
        response = client.get("/healthz", headers={"X-Request-ID": "req-abc-123"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-abc-123"

    context = types.SimpleNamespace(message=types.SimpleNamespace(name="search_runbooks"))

    async def call_next(_context):
        return "unexpected"

    # FastMCP prepends its own built-in middleware; ours must be in order:
    # tool span outermost, then the authorization check.
    chain = [
        item for item in app.middleware
        if isinstance(item, (telemetry_module.ToolSpanMiddleware, server.ScopeMiddleware))
    ]
    assert isinstance(chain[0], telemetry_module.ToolSpanMiddleware)
    assert isinstance(chain[1], server.ScopeMiddleware)

    async def run_chain():
        async def inner(ctx):
            return await chain[1].on_call_tool(ctx, call_next)
        return await chain[0].on_call_tool(context, inner)

    with pytest.raises(server.ToolError):
        asyncio.run(run_chain())

    spans = {span.name: span for span in tel.span_exporter.get_finished_spans()}
    assert spans["mcp.tool"].attributes["gateway.outcome"] == "denied"
    assert spans["mcp.tool"].attributes["mcp.tool.name"] == "search_runbooks"
    assert "http.request" in spans
    assert app.readiness_checks()["auth_configured"] is False


def _ready_gauge_value(tel) -> int | None:
    for rm in tel.metric_reader.get_metrics_data().resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != "gateway.ready":
                    continue
                for point in metric.data.data_points:
                    return point.value
    return None


def test_ready_gauge_matches_readyz_when_fully_configured(tmp_path):
    """The gateway.ready gauge must agree with /readyz: both apply the same
    readiness contract (first four checks true and no fault injected), not
    the weaker all(checks.values()) over all five, which also folds in
    fault_injected as if it needed to be True."""
    import telemetry as telemetry_module
    from starlette.testclient import TestClient

    tel = telemetry_module.configure_telemetry(exporter="memory")
    app = server.build_app(config(tmp_path), ALLOWLIST, with_auth=True, telemetry=tel)

    assert _ready_gauge_value(tel) == 1
    with TestClient(server.create_http_app(app, tel)) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_ready_gauge_matches_readyz_on_fault_injection(tmp_path):
    import telemetry as telemetry_module
    from starlette.testclient import TestClient

    cfg = config(tmp_path)
    cfg["fault_inject"] = "not_ready"
    tel = telemetry_module.configure_telemetry(exporter="memory")
    app = server.build_app(cfg, ALLOWLIST, with_auth=True, telemetry=tel)

    assert _ready_gauge_value(tel) == 0
    with TestClient(server.create_http_app(app, tel)) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
