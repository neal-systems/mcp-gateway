"""Tests for scripts/ci/check_example_env.py against isolated tmp copies."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_SCRIPT = REPO_ROOT / "scripts" / "ci" / "check_example_env.py"


def _make_repo_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    dest.mkdir()
    shutil.copytree(REPO_ROOT / "app", dest / "app")
    shutil.copy2(REPO_ROOT / "config.example.env", dest / "config.example.env")
    return dest


def _run_check(repo_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK_SCRIPT), str(repo_dir)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_passes_on_real_files(tmp_path: Path) -> None:
    repo = _make_repo_copy(tmp_path)
    result = _run_check(repo)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_fails_when_referenced_name_missing(tmp_path: Path) -> None:
    repo = _make_repo_copy(tmp_path)
    env_path = repo / "config.example.env"
    lines = env_path.read_text().splitlines()
    lines = [line for line in lines if not line.startswith("GATEWAY_HOST=")]
    env_path.write_text("\n".join(lines) + "\n")

    result = _run_check(repo)
    assert result.returncode != 0
    assert "GATEWAY_HOST" in result.stderr


def test_fails_when_value_looks_like_a_live_value(tmp_path: Path) -> None:
    repo = _make_repo_copy(tmp_path)
    env_path = repo / "config.example.env"
    target_key = "GITHUB_CLIENT" + "_SECRET"
    not_a_real_value = "".join(["abc123", "def456"])
    lines = env_path.read_text().splitlines()
    rewritten = []
    for line in lines:
        if line.startswith(target_key + "="):
            rewritten.append(f"{target_key}={not_a_real_value}")
        else:
            rewritten.append(line)
    env_path.write_text("\n".join(rewritten) + "\n")

    result = _run_check(repo)
    assert result.returncode != 0
    assert target_key in result.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
