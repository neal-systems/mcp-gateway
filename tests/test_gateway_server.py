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
    return {
        "base_url": "https://gateway.example.com",
        "github_client_id": "obviously-fake-client-id",
        "github_client_secret": "obviously-fake-client-secret",
        "jwt_signing_key": "test-signing-value-not-for-use",
        "client_storage": str(tmp_path / "oauth"),
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
