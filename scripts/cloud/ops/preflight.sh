#!/usr/bin/env bash
# Report readiness to deploy: tool availability, AWS identity, SSM
# parameter names, whether a given image digest exists on GHCR, and whether
# the repo's `demo` environment and deploy variables are configured.
# Reports only; never creates anything.
# Usage: preflight.sh [--image-digest sha256:<64hex>] [--region R]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_lib.sh"

ops_parse_region "$@"
set -- "${REMAINING_ARGS[@]}"

image_digest=""
while [ $# -gt 0 ]; do
  case "$1" in
    --image-digest) image_digest="$2"; shift 2 ;;
    --image-digest=*) image_digest="${1#--image-digest=}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

ops_require_identity
ops_region_args

missing_tools=""
for bin in aws terraform docker gh; do
  command -v "$bin" >/dev/null 2>&1 || missing_tools="${missing_tools} ${bin}"
done

account_masked="$(ops_mask_account "$ACCOUNT_ID")"

ssm_param_names=$(aws ssm describe-parameters "${AWS_REGION_ARGS[@]}" \
  --parameter-filters "Key=Name,Option=BeginsWith,Values=/mcp-gateway/demo/" \
  --query 'Parameters[].Name' --output json 2>/dev/null || echo '[]')

image_exists="unknown"
if [ -n "$image_digest" ]; then
  if docker manifest inspect "ghcr.io/neal-systems/mcp-gateway@${image_digest}" >/dev/null 2>&1; then
    image_exists="true"
  else
    image_exists="false"
  fi
fi

environment_exists="false"
if gh api "repos/neal-systems/mcp-gateway/environments/demo" >/dev/null 2>&1; then
  environment_exists="true"
fi

# gh prints the API error body on stdout AND exits non-zero, so the fallback
# must replace the output, not be appended to it.
if ! variables_json=$(gh api "repos/neal-systems/mcp-gateway/environments/demo/variables" 2>/dev/null); then
  variables_json='{"variables":[]}'
fi

PF_MISSING_TOOLS="${missing_tools}" \
PF_ACCOUNT_MASKED="${account_masked}" \
PF_REGION="${REGION:-}" \
PF_SSM_NAMES="${ssm_param_names}" \
PF_IMAGE_DIGEST="${image_digest}" \
PF_IMAGE_EXISTS="${image_exists}" \
PF_ENV_EXISTS="${environment_exists}" \
PF_VARIABLES_JSON="${variables_json}" \
python3 <<'PYEOF'
import json
import os

missing = os.environ.get("PF_MISSING_TOOLS", "").split()
ssm_names = json.loads(os.environ.get("PF_SSM_NAMES") or "[]")
variables = json.loads(os.environ.get("PF_VARIABLES_JSON") or '{"variables": []}').get("variables", [])
var_names = {v.get("name") for v in variables}

out = {
    "tools_present": {t: (t not in missing) for t in ("aws", "terraform", "docker", "gh")},
    "account_masked": os.environ.get("PF_ACCOUNT_MASKED"),
    "region": os.environ.get("PF_REGION") or None,
    "ssm_parameters_present": ssm_names,
    "image_digest": os.environ.get("PF_IMAGE_DIGEST") or None,
    "image_digest_exists_on_ghcr": os.environ.get("PF_IMAGE_EXISTS"),
    "environment_demo_exists": os.environ.get("PF_ENV_EXISTS") == "true",
    "variable_aws_deploy_role_arn_present": "AWS_DEPLOY_ROLE_ARN" in var_names,
    "variable_aws_region_present": "AWS_REGION" in var_names,
}
print(json.dumps(out, indent=2))
PYEOF
