"""The final-pass redactor blanks secret carriers and nothing else."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "redact_tool", Path(__file__).parents[1] / "scripts" / "cloud" / "ops" / "redact.py"
)
redact_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(redact_tool)


def _kv(key: str, value: str, sep: str = "=") -> str:
    return key + sep + value


def test_secret_carriers_are_blanked():
    text = " ".join([
        _kv("client_secret", "SENTINEL-CS"),
        _kv("code", "SENTINELCODE"),
        "Authorization: Bearer SENTINEL-BEARER-TOKEN",
        "gho_SENTINELSENTINEL1234",
        "a" * 64,
    ])
    out = redact_tool.redact(text)
    assert "SENTINEL" not in out
    assert "a" * 64 not in out


def test_status_codes_and_ordinary_fields_survive():
    line = '{"http.response.status_code": 401, "zip_code": "50000", "status_code=200"}'
    out = redact_tool.redact(line)
    assert '"http.response.status_code": 401' in out
    assert "50000" in out
    assert "status_code=200" in out
