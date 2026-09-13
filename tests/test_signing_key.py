"""Signing-key generate-once contract (CONTRACTS.md section 1).

The regression test at the top of this file is the one required by the work
package: it proves that an operator who leaves GATEWAY_JWT_SIGNING_KEY unset
(the PLACEHOLDER value) gets a generated key file under the state dir instead
of a startup ConfigError. It must fail against the pre-fix code, where
`credentials_are_placeholders` folds the signing key into the placeholder
check and `_build_auth` raises before the generate-once branch is reached.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import gateway_server as server
import scope

OPERATOR_ID = "111111111"
ALLOWLIST = {OPERATOR_ID: scope.ROLE_OPERATOR}


def base_config(tmp_path: Path) -> dict:
    state_dir = tmp_path / "state"
    return {
        "base_url": "https://gateway.example.com",
        "github_client_id": "obviously-fake-client-id",
        "github_client_secret": "obviously-fake-client-secret",
        "jwt_signing_key": server.PLACEHOLDER,
        "state_dir": str(state_dir),
        "client_storage": str(state_dir / "client_storage"),
        "jwt_signing_key_file": str(state_dir / "jwt_signing_key"),
        "host": "127.0.0.1",
        "port": 8080,
        "fault_inject": None,
        "release_id": "dev",
        "sample_data": Path(__file__).parents[1] / "app" / "sample_data.json",
    }


def test_unset_signing_key_generates_key_file_under_state_dir(tmp_path):
    """Regression test: placeholder key must NOT raise ConfigError; it must
    take the generate-once path and leave a key file under the state dir."""
    cfg = base_config(tmp_path)
    key_path = Path(cfg["jwt_signing_key_file"])

    assert not key_path.exists()
    server.build_app(cfg, ALLOWLIST, with_auth=True)
    assert key_path.is_file(), "generate-once path did not create a key file"
    assert key_path.read_text(encoding="utf-8").strip() != ""


def test_generated_key_file_has_mode_0600(tmp_path):
    cfg = base_config(tmp_path)
    key_path = Path(cfg["jwt_signing_key_file"])
    server.build_app(cfg, ALLOWLIST, with_auth=True)
    mode = stat.S_IMODE(key_path.stat().st_mode)
    assert mode == 0o600


def test_second_build_reuses_same_key(tmp_path):
    cfg = base_config(tmp_path)
    key_path = Path(cfg["jwt_signing_key_file"])
    server.build_app(cfg, ALLOWLIST, with_auth=True)
    first = key_path.read_text(encoding="utf-8")

    server.build_app(cfg, ALLOWLIST, with_auth=True)
    second = key_path.read_text(encoding="utf-8")

    assert first == second


def test_existing_nonempty_key_file_is_never_overwritten(tmp_path):
    cfg = base_config(tmp_path)
    key_path = Path(cfg["jwt_signing_key_file"])
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text("preexisting-key-value-that-must-survive", encoding="utf-8")

    server.build_app(cfg, ALLOWLIST, with_auth=True)

    assert key_path.read_text(encoding="utf-8") == "preexisting-key-value-that-must-survive"


def test_explicit_key_of_valid_length_is_used_verbatim_and_no_file_written(tmp_path):
    cfg = base_config(tmp_path)
    cfg["jwt_signing_key"] = "a" * 32
    key_path = Path(cfg["jwt_signing_key_file"])

    server.build_app(cfg, ALLOWLIST, with_auth=True)

    assert not key_path.exists()


def test_explicit_key_too_short_raises_config_error(tmp_path):
    cfg = base_config(tmp_path)
    cfg["jwt_signing_key"] = "too-short"

    with pytest.raises(scope.ConfigError):
        server.build_app(cfg, ALLOWLIST, with_auth=True)


def test_placeholder_client_secret_still_raises_config_error(tmp_path):
    cfg = base_config(tmp_path)
    cfg["github_client_secret"] = server.PLACEHOLDER

    with pytest.raises(scope.ConfigError):
        server.build_app(cfg, ALLOWLIST, with_auth=True)


def test_state_dir_owned_by_another_user_is_a_warning_not_a_failure(monkeypatch, tmp_path, caplog):
    """A bind-mounted state directory the app cannot chmod (host-owned) must
    still allow startup; readiness separately enforces writability."""
    import logging

    real_chmod = os.chmod
    state_dir = tmp_path / "host-owned-state"

    def chmod_denied(path, mode, *args, **kwargs):
        if Path(path) == state_dir:
            raise PermissionError(1, "Operation not permitted", str(path))
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", chmod_denied)
    cfg = config(tmp_path)
    cfg["state_dir"] = str(state_dir)
    cfg["client_storage"] = str(state_dir / "client_storage")
    cfg["jwt_signing_key_file"] = str(state_dir / "jwt_signing_key")
    cfg["jwt_signing_key"] = server.PLACEHOLDER
    with caplog.at_level(logging.WARNING):
        app = server.build_app(cfg, ALLOWLIST, with_auth=True)
    assert app.readiness_checks()["state_dir_writable"] is True
    assert (state_dir / "jwt_signing_key").is_file()
    assert any("could not set mode 0700" in rec.message for rec in caplog.records)
