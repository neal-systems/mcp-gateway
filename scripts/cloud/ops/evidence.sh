#!/usr/bin/env bash
# Collect a point-in-time evidence bundle for the demo deployment: the
# remote gateway-release evidence output plus AWS resource inventory
# filtered to the mcp-gateway-demo project tag.
# Usage: evidence.sh --out DIR [--region R]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_lib.sh"

ops_parse_region "$@"
set -- "${REMAINING_ARGS[@]}"

out_dir=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out) out_dir="$2"; shift 2 ;;
    --out=*) out_dir="${1#--out=}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$out_dir" ]; then
  echo "usage: $0 --out DIR [--region R]" >&2
  exit 2
fi

ops_require_identity
instance_id=$(ops_find_instance)
ops_require_project_tag "$instance_id"
ops_region_args

mkdir -p "$out_dir"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

remote_invocation=$("${SCRIPT_DIR}/ssm_invoke.sh" "$instance_id" "/opt/mcp-gateway/bin/gateway-release evidence" "local evidence")
echo "$remote_invocation" > "${out_dir}/evidence-remote-${timestamp}.json"

aws ec2 describe-instances "${AWS_REGION_ARGS[@]}" \
  --filters "Name=tag:Project,Values=mcp-gateway-demo" \
  > "${out_dir}/describe-instances-${timestamp}.json"

aws ec2 describe-volumes "${AWS_REGION_ARGS[@]}" \
  --filters "Name=tag:Project,Values=mcp-gateway-demo" \
  > "${out_dir}/describe-volumes-${timestamp}.json"

aws ec2 describe-addresses "${AWS_REGION_ARGS[@]}" \
  --filters "Name=tag:Project,Values=mcp-gateway-demo" \
  > "${out_dir}/describe-addresses-${timestamp}.json"

python3 -c '
import json, sys
print(json.dumps({
    "out_dir": sys.argv[1],
    "timestamp": sys.argv[2],
    "files": [
        f"evidence-remote-{sys.argv[2]}.json",
        f"describe-instances-{sys.argv[2]}.json",
        f"describe-volumes-{sys.argv[2]}.json",
        f"describe-addresses-{sys.argv[2]}.json",
    ],
}))
' "$out_dir" "$timestamp"
