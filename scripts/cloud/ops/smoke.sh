#!/usr/bin/env bash
# External HTTPS smoke checks against a deployed gateway hostname.
# Usage: smoke.sh --hostname gateway.example.com [--region R]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_lib.sh"

ops_parse_region "$@"
set -- "${REMAINING_ARGS[@]}"

hostname=""
while [ $# -gt 0 ]; do
  case "$1" in
    --hostname) hostname="$2"; shift 2 ;;
    --hostname=*) hostname="${1#--hostname=}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$hostname" ]; then
  echo "usage: $0 --hostname H [--region R]" >&2
  exit 2
fi

ops_require_identity
instance_id=$(ops_find_instance)
ops_require_project_tag "$instance_id"

base="https://${hostname}"

healthz_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${base}/healthz" || echo "000")
readyz_status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${base}/readyz" || echo "000")

mcp_headers=$(curl -s -D - -o /dev/null --max-time 10 -X POST "${base}/mcp" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' || true)
mcp_status=$(printf '%s' "$mcp_headers" | head -1 | grep -oE '[0-9]{3}' | head -1)
mcp_status="${mcp_status:-000}"
www_authenticate_present="false"
if printf '%s' "$mcp_headers" | grep -qi '^www-authenticate:'; then
  www_authenticate_present="true"
fi

oauth_metadata_status="000"
for path in "/.well-known/oauth-protected-resource" "/.well-known/oauth-authorization-server"; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${base}${path}" || echo "000")
  if [ "$code" = "200" ]; then
    oauth_metadata_status="200"
    break
  fi
  oauth_metadata_status="$code"
done

tls_ok="false"
if curl -sS --fail-with-body --max-time 10 -o /dev/null "${base}/healthz" 2>/dev/null; then
  tls_ok="true"
fi

all_pass="false"
if [ "$healthz_status" = "200" ] && [ "$readyz_status" = "200" ] && [ "$mcp_status" = "401" ] \
  && [ "$www_authenticate_present" = "true" ] && [ "$oauth_metadata_status" = "200" ] && [ "$tls_ok" = "true" ]; then
  all_pass="true"
fi

python3 -c '
import json, sys
d = dict(zip(
    ["hostname","healthz_status","readyz_status","mcp_status","www_authenticate_present","oauth_metadata_status","tls_ok","pass"],
    sys.argv[1:9],
))
d["healthz_status"] = d["healthz_status"]
d["www_authenticate_present"] = d["www_authenticate_present"] == "true"
d["tls_ok"] = d["tls_ok"] == "true"
d["pass"] = d["pass"] == "true"
print(json.dumps(d, indent=2))
' "$hostname" "$healthz_status" "$readyz_status" "$mcp_status" "$www_authenticate_present" "$oauth_metadata_status" "$tls_ok" "$all_pass"

[ "$all_pass" = "true" ]
