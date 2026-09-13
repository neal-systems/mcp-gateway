#!/usr/bin/env bash
# Redeploy the known-good release on the demo instance.
# Usage: rollback.sh [--region R]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_lib.sh"

ops_parse_region "$@"

ops_require_identity
instance_id=$(ops_find_instance)
ops_require_project_tag "$instance_id"

exec "${SCRIPT_DIR}/ssm_invoke.sh" "$instance_id" "/opt/mcp-gateway/bin/gateway-release rollback" "local rollback"
