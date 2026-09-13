#!/usr/bin/env bash
# Run every LOCAL evidence class (unit, mutation negative control, container)
# through the acceptance recorder. Safe to re-run; each check upserts by id.
# Usage: scripts/evidence/local_checks.sh [python]   (default .venv/bin/python)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${1:-.venv/bin/python}"
REC="$PY scripts/evidence/record.py"
cd "$ROOT"

# 1. Full suite from the current checkout.
$REC run --id unit.suite --title "Unit and integration tests" --class unit --env local \
  -- "$PY" -m pytest -q

# 2. Regression that proves the generate-once signing-key path is reachable.
$REC run --id unit.signing_key_regression --title "Signing key generate-once regression" \
  --class unit --env local -- "$PY" -m pytest -q tests/test_signing_key.py

# 3. Negative control: mutate authorization to allow everything, in a scratch
#    copy, and require the direct-call tests to FAIL. A control that would pass
#    either way proves nothing.
scratch="$(mktemp -d)"
cp -r app "$scratch/app"
"$PY" - "$scratch/app/scope.py" <<'PYEOF'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); s = p.read_text()
s = s.replace("    return tool_name in allowed_tools(role)", "    return True  # MUTANT: authorization disabled")
assert "MUTANT" in s
p.write_text(s)
PYEOF
$REC run --id unit.negative_control_authz --title "Negative control: authz disabled must fail tests" \
  --class unit --env local --expect-fail --notes "scope.is_tool_allowed mutated to always True in a scratch copy" \
  -- env PYTHONPATH="$scratch/app" "$PY" -m pytest -q -p no:cacheprovider -o pythonpath="$scratch/app" \
       tests/test_gateway_server.py tests/test_scope.py
rm -rf "$scratch"

# 4. Redaction with fake sentinel secrets.
$REC run --id unit.redaction_sentinels --title "Telemetry redaction of fake sentinel secrets" \
  --class unit --env local -- "$PY" -m pytest -q tests/test_telemetry.py -k "redact or sentinel or argument"

# 5. Container: build, then smoke (healthz 200, readyz 200, /mcp 401).
docker build -q -t mcp-gateway:evidence ./app >/dev/null
digest="$(docker image inspect --format '{{.Id}}' mcp-gateway:evidence)"
$REC run --id container.smoke --title "Container liveness, readiness, unauthenticated denial" \
  --class container --env local --image-digest "$digest" -- scripts/ci/container_smoke.sh mcp-gateway:evidence

# 6. Container: restart preserves the generated signing key and readiness.
$REC run --id container.restart_persistence --title "Container restart keeps signing key and readiness" \
  --class container --env local --image-digest "$digest" -- scripts/evidence/container_restart_check.sh mcp-gateway:evidence

# 7. Container: no sentinel reaches the log stream, and every line is JSON.
$REC run --id container.log_redaction --title "Container log stream is JSON and redacted" \
  --class container --env local --image-digest "$digest" -- scripts/evidence/container_log_check.sh mcp-gateway:evidence

# 8. Container: the whole release state machine against real containers:
#    push/pull by digest, deploy A, A-to-B with a measured interruption,
#    a candidate that never becomes ready (exit 3) rolled back automatically,
#    rollback, status, smoke, evidence, and restart persistence.
$REC run --id container.release_drill --title "Local release drill: deploy, upgrade, failed candidate, rollback" \
  --class container --env local --image-digest "$digest" -- bash tests/cloud/local_release_drill.sh

$REC render
$REC summary
