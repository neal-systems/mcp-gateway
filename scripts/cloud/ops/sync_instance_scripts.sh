#!/usr/bin/env bash
# Push the working-tree copies of scripts/cloud/instance/* to the demo host
# over SSM Run Command. user_data_replace_on_change is false on purpose, so
# this is the supported way to update the instance scripts without recycling
# the host. Prints the remote invocation JSON; exit code = remote exit code.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/_lib.sh"
ops_parse_region "$@"
ops_require_identity
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
instance_id="$(ops_find_demo_instance)"
ops_require_project_tag "$instance_id"
b64() { gzip -c "$1" | base64 -w0; }
gr="$(b64 "$REPO_ROOT/scripts/cloud/instance/gateway-release")"
bs="$(b64 "$REPO_ROOT/scripts/cloud/instance/bootstrap.sh")"
cmd="set -e; umask 022; echo '$gr' | base64 -d | gunzip > /opt/mcp-gateway/bin/gateway-release.new; echo '$bs' | base64 -d | gunzip > /opt/mcp-gateway/bin/bootstrap.sh.new; bash -n /opt/mcp-gateway/bin/gateway-release.new; bash -n /opt/mcp-gateway/bin/bootstrap.sh.new; chmod 0755 /opt/mcp-gateway/bin/gateway-release.new /opt/mcp-gateway/bin/bootstrap.sh.new; mv /opt/mcp-gateway/bin/gateway-release.new /opt/mcp-gateway/bin/gateway-release; mv /opt/mcp-gateway/bin/bootstrap.sh.new /opt/mcp-gateway/bin/bootstrap.sh; sha256sum /opt/mcp-gateway/bin/gateway-release /opt/mcp-gateway/bin/bootstrap.sh"
bash "$HERE/ssm_invoke.sh" "$instance_id" "$cmd" "sync instance scripts"
