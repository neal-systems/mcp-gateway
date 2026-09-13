"""Health endpoints (CONTRACTS.md section 2)."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import gateway_server as server
import scope

OPERATOR_ID = "111111111"
ALLOWLIST = {OPERATOR_ID: scope.ROLE_OPERATOR}


def config(tmp_path: Path, **overrides) -> dict:
    state_dir = tmp_path / "state"
    cfg = {
        "base_url": "https://gateway.example.com",
        "github_client_id": "obviously-fake-client-id",
        "github_client_secret": "obviously-fake-client-secret",
        "jwt_signing_key": "a" * 32,
        "state_dir": str(state_dir),
        "client_storage": str(state_dir / "client_storage"),
        "jwt_signing_key_file": str(state_dir / "jwt_signing_key"),
        "release_id": "dev",
        "fault_inject": None,
        "host": "127.0.0.1",
        "port": 8080,
        "sample_data": Path(__file__).parents[1] / "app" / "sample_data.json",
    }
    cfg.update(overrides)
    return cfg


def client_for(cfg: dict, with_auth: bool = True) -> TestClient:
    app = server.build_app(cfg, ALLOWLIST, with_auth=with_auth)
    http_app = server.create_http_app(app)
    return TestClient(http_app)


def test_healthz_is_always_ok_with_no_store(tmp_path):
    cfg = config(tmp_path)
    with client_for(cfg) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_readyz_ready_when_fully_configured(tmp_path):
    cfg = config(tmp_path)
    with client_for(cfg) as client:
        response = client.get("/readyz")
    body = response.json()
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert body["status"] == "ready"
    assert body["checks"] == {
        "sample_data": True,
        "state_dir_writable": True,
        "signing_key": True,
        "auth_configured": True,
        "fault_injected": False,
    }


def test_readyz_not_ready_without_auth(tmp_path):
    cfg = config(tmp_path)
    with client_for(cfg, with_auth=False) as client:
        response = client.get("/readyz")
    body = response.json()
    assert response.status_code == 503
    assert body["status"] == "not_ready"
    assert body["checks"]["auth_configured"] is False
    assert body["checks"]["signing_key"] is False


def test_readyz_not_ready_on_fault_injection(tmp_path):
    cfg = config(tmp_path, fault_inject="not_ready")
    with client_for(cfg) as client:
        response = client.get("/readyz")
    body = response.json()
    assert response.status_code == 503
    assert body["status"] == "not_ready"
    assert body["checks"]["fault_injected"] is True


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permissions")
def test_readyz_not_ready_when_state_dir_unwritable(tmp_path):
    cfg = config(tmp_path)
    app = server.build_app(cfg, ALLOWLIST, with_auth=True)
    http_app = server.create_http_app(app)
    state_dir = Path(cfg["state_dir"])
    os.chmod(state_dir, 0o500)
    try:
        with TestClient(http_app) as client:
            response = client.get("/readyz")
        body = response.json()
        assert response.status_code == 503
        assert body["checks"]["state_dir_writable"] is False
    finally:
        os.chmod(state_dir, 0o700)


def test_readiness_checks_exposed_on_built_app(tmp_path):
    cfg = config(tmp_path)
    app = server.build_app(cfg, ALLOWLIST, with_auth=True)
    checks = app.readiness_checks()
    assert set(checks) == {
        "sample_data",
        "state_dir_writable",
        "signing_key",
        "auth_configured",
        "fault_injected",
    }
    assert all(isinstance(value, bool) for value in checks.values())


def test_health_bodies_never_leak_secrets_or_identity(tmp_path):
    cfg = config(tmp_path)
    forbidden = [
        cfg["state_dir"],
        cfg["jwt_signing_key"],
        cfg["github_client_id"],
        OPERATOR_ID,
    ]
    with client_for(cfg) as client:
        healthz_text = client.get("/healthz").text
        readyz_text = client.get("/readyz").text
    for secret in forbidden:
        assert secret not in healthz_text
        assert secret not in readyz_text


def test_unauthenticated_mcp_call_is_rejected(tmp_path):
    cfg = config(tmp_path)
    with client_for(cfg) as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
        )
    assert response.status_code in (401, 403)
