#!/usr/bin/env python3
"""Guard config.example.env against drift and accidental live values.

Every GATEWAY_/GITHUB_ name referenced as a string literal in app/*.py must
appear in config.example.env, and no value there may look like a live
credential: non-blank values must start with YOUR_ or be one of the
documented, non-sensitive defaults.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

NAME_RE = re.compile(r'"((?:GATEWAY|GITHUB)_[A-Z0-9_]+)"')

# Documented, non-sensitive example values (README.md / docs/cloud/CONTRACTS.md
# defaults). Anything else non-blank and non-YOUR_ is treated as suspicious.
ALLOWED_DEFAULT_VALUES = {
    "gateway.example.com",
    "https://gateway.example.com",
    "0.0.0.0",
    "8080",
    "/data/state",
    "dev",
    "/app/sample_data.json",
}


def referenced_names(app_dir: Path) -> set[str]:
    names: set[str] = set()
    for path in sorted(app_dir.glob("*.py")):
        names |= set(NAME_RE.findall(path.read_text()))
    return names


def parse_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def main() -> int:
    repo_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    app_dir = repo_root / "app"
    example_env_path = repo_root / "config.example.env"

    names = referenced_names(app_dir)
    env = parse_env(example_env_path)

    errors: list[str] = []
    for name in sorted(names):
        if name not in env:
            errors.append(f"{name} is referenced in app/*.py but missing from config.example.env")

    for key, value in env.items():
        if not value or value.startswith("YOUR_"):
            continue
        if value not in ALLOWED_DEFAULT_VALUES:
            errors.append(f"{key}={value!r} in config.example.env is not a documented default")

    if errors:
        for err in errors:
            print(f"error: {err}", file=sys.stderr)
        return 1

    print(f"config.example.env ok: {len(names)} referenced names present, {len(env)} values checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
