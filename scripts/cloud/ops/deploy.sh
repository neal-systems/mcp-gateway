#!/usr/bin/env bash
# Build a release manifest and deploy it to the demo instance over SSM.
# Usage: deploy.sh --release-id sha-<12hex> --image-digest sha256:<64hex> [--drill-not-ready] [--region R]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_lib.sh"

ops_parse_region "$@"
set -- "${REMAINING_ARGS[@]}"

release_id=""
image_digest=""
drill_not_ready="false"
while [ $# -gt 0 ]; do
  case "$1" in
    --release-id) release_id="$2"; shift 2 ;;
    --release-id=*) release_id="${1#--release-id=}"; shift ;;
    --image-digest) image_digest="$2"; shift 2 ;;
    --image-digest=*) image_digest="${1#--image-digest=}"; shift ;;
    --drill-not-ready) drill_not_ready="true"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if ! [[ "$release_id" =~ ^sha-[0-9a-f]{12}$ ]]; then
  echo "invalid --release-id '${release_id}' (expected sha-<12 hex>)" >&2
  exit 2
fi
if ! [[ "$image_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "invalid --image-digest '${image_digest}' (expected sha256:<64 hex>)" >&2
  exit 2
fi

ops_require_identity
instance_id=$(ops_find_instance)
ops_require_project_tag "$instance_id"

git_sha=$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

manifest_json=$(python3 -c '
import json, sys
release_id, digest, git_sha, built_at = sys.argv[1:5]
print(json.dumps({
    "schema": 1,
    "release_id": release_id,
    "image": f"ghcr.io/neal-systems/mcp-gateway@{digest}",
    "git_sha": git_sha,
    "built_at": built_at,
    "builder": "local",
}))
' "$release_id" "$image_digest" "$git_sha" "$built_at")
manifest_b64=$(printf '%s' "$manifest_json" | base64 -w0)

cmd="/opt/mcp-gateway/bin/gateway-release deploy --manifest-b64 ${manifest_b64}"
if [ "$drill_not_ready" = "true" ]; then
  cmd="${cmd} --drill-not-ready"
fi

exec "${SCRIPT_DIR}/ssm_invoke.sh" "$instance_id" "$cmd" "local deploy ${release_id}"
