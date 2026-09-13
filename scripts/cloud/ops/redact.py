#!/usr/bin/env python3
"""Final structured redaction for anything that leaves the instance for a
public place (job summaries, evidence bundles). Reads stdin, writes stdout.

The app redacts at source and gateway-release runs a second pass; this third
pass exists because a job summary on a public repository is the one place a
mistake cannot be taken back. Patterns mirror app/telemetry.py."""
import re
import sys

KEYS = (
    "authorization|cookie|set-cookie|code|token|access_token|refresh_token|id_token|"
    "client_secret|client_id|signing_key|jwt_signing_key|password|secret|api_key"
)
PATTERNS = [
    # Token shapes first: the key=value rule below would otherwise consume the
    # word "Bearer" and leave the token standing.
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{8,}"), "[REDACTED]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{8,}"), "[REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "[REDACTED]"),
    # The lookbehind keeps status_code, zip_code and the like intact: only a
    # whole key named code/token/... is a secret carrier.
    (re.compile(rf'(?<![A-Za-z0-9_.])("?(?:{KEYS})"?\s*[:=]\s*"?)([^"&\s,;]+)', re.I), r"\1[REDACTED]"),
    (re.compile(r"\b[0-9a-fA-F]{64}\b"), "[REDACTED-64HEX]"),
]


def redact(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


if __name__ == "__main__":
    sys.stdout.write(redact(sys.stdin.read()))
