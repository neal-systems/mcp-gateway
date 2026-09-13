#!/usr/bin/env bash
# Wrapper around `terraform destroy` for infra/aws/demo. Prints the resource
# inventory first, requires --yes, then inventories what is left afterward.
# Never touches bootstrap resources (state bucket, OIDC role) or SSM
# parameters unless --delete-parameters is also given.
# Usage: teardown.sh --yes [--delete-parameters] [--region R]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_lib.sh"

ops_parse_region "$@"
set -- "${REMAINING_ARGS[@]}"

confirmed="false"
delete_parameters="false"
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) confirmed="true"; shift ;;
    --delete-parameters) delete_parameters="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

ops_require_identity
ops_region_args

inventory_json() {
  aws ec2 describe-instances "${AWS_REGION_ARGS[@]}" --filters "Name=tag:Project,Values=mcp-gateway-demo" > /tmp/teardown-instances.$$.json
  aws ec2 describe-volumes "${AWS_REGION_ARGS[@]}" --filters "Name=tag:Project,Values=mcp-gateway-demo" > /tmp/teardown-volumes.$$.json
  aws ec2 describe-snapshots "${AWS_REGION_ARGS[@]}" --owner-ids self --filters "Name=tag:Project,Values=mcp-gateway-demo" > /tmp/teardown-snapshots.$$.json
  aws ec2 describe-addresses "${AWS_REGION_ARGS[@]}" --filters "Name=tag:Project,Values=mcp-gateway-demo" > /tmp/teardown-addresses.$$.json
  aws ssm describe-parameters "${AWS_REGION_ARGS[@]}" \
    --parameter-filters "Key=Name,Option=BeginsWith,Values=/mcp-gateway/demo/" > /tmp/teardown-params.$$.json
  gh api "repos/neal-systems/mcp-gateway/packages/container/mcp-gateway/versions" > /tmp/teardown-ghcr.$$.json 2>/dev/null \
    || echo '[]' > /tmp/teardown-ghcr.$$.json

  python3 -c '
import json, sys
files = sys.argv[1:6]
names = ["instances", "volumes", "snapshots", "addresses", "ssm_parameters"]
out = {}
for name, path in zip(names, files):
    with open(path) as fh:
        out[name] = json.load(fh)
print(json.dumps(out, indent=2))
' /tmp/teardown-instances.$$.json /tmp/teardown-volumes.$$.json /tmp/teardown-snapshots.$$.json /tmp/teardown-addresses.$$.json /tmp/teardown-params.$$.json
}

echo "Resource inventory before destroy:" >&2
inventory_json

if [ "$confirmed" != "true" ]; then
  echo "refusing to destroy: pass --yes after reviewing the inventory above" >&2
  rm -f /tmp/teardown-*.$$.json
  exit 2
fi

echo "running terraform destroy for infra/aws/demo" >&2
# --yes was already required above; Terraform must not prompt again (no TTY under automation).
terraform -chdir="${REPO_ROOT}/infra/aws/demo" destroy -auto-approve -input=false

echo "Residual inventory after destroy:" >&2
residual=$(inventory_json)
echo "$residual"

if [ "$delete_parameters" = "true" ]; then
  echo "deleting SSM parameters under /mcp-gateway/demo/ (--delete-parameters given)" >&2
  names=$(aws ssm describe-parameters "${AWS_REGION_ARGS[@]}" \
    --parameter-filters "Key=Name,Option=BeginsWith,Values=/mcp-gateway/demo/" \
    --query 'Parameters[].Name' --output text)
  for name in $names; do
    aws ssm delete-parameter "${AWS_REGION_ARGS[@]}" --name "$name"
  done
else
  echo "SSM parameters under /mcp-gateway/demo/ were left in place (pass --delete-parameters to remove them)" >&2
fi

echo "bootstrap resources (state bucket, OIDC role) were not touched" >&2

rm -f /tmp/teardown-*.$$.json
