#!/usr/bin/env bash
# Send a shell command to one instance via SSM RunCommand, wait for it to
# finish, and print the invocation JSON on stdout. Exit code mirrors the
# remote command's exit code so a caller can distinguish "ran and failed"
# (e.g. gateway-release exit 3, a known rollback outcome) from "never ran".
#
# Usage: ssm_invoke.sh <instance-id> <command> [comment]
#
# Shared by .github/workflows/deploy.yml and any local operator so both
# paths exercise the exact same code.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_lib.sh"

if [ $# -lt 2 ]; then
  echo "usage: $0 <instance-id> <command> [comment]" >&2
  exit 2
fi

INSTANCE_ID="$1"
COMMAND_STR="$2"
COMMENT="${3:-manual}"

ops_parse_region
ops_require_identity
ops_require_project_tag "$INSTANCE_ID"
ops_region_args

command_id=$(aws ssm send-command "${AWS_REGION_ARGS[@]}" \
  --document-name AWS-RunShellScript \
  --instance-ids "$INSTANCE_ID" \
  --comment "$COMMENT" \
  --timeout-seconds 600 \
  --parameters commands="[\"${COMMAND_STR}\"]" \
  --query 'Command.CommandId' --output text)
if [ -z "$command_id" ] || [ "$command_id" = "None" ]; then
  echo "send-command did not return a CommandId" >&2
  exit 1
fi
echo "dispatched command ${command_id} to ${INSTANCE_ID}" >&2

deadline=$((SECONDS + 600))
status="Pending"
invocation="{}"
while [ "$status" = "Pending" ] || [ "$status" = "InProgress" ] || [ "$status" = "Delayed" ]; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "timed out after 10 minutes waiting for command ${command_id}" >&2
    exit 1
  fi
  sleep 5
  invocation=$(aws ssm get-command-invocation "${AWS_REGION_ARGS[@]}" \
    --command-id "$command_id" --instance-id "$INSTANCE_ID" 2>&1) || {
    echo "get-command-invocation failed: ${invocation}" >&2
    exit 1
  }
  status=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("Status","Unknown"))' "$invocation")
done

echo "$invocation"

exit_code=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("ResponseCode", 1))' "$invocation")
exit "$exit_code"
