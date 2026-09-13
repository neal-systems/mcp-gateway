#!/usr/bin/env bash
# put_parameter.sh - store one demo secret as an SSM SecureString parameter.
#
#   scripts/cloud/put_parameter.sh github_client_secret ./secret.txt
#   printf '%s' "$value" | scripts/cloud/put_parameter.sh github_client_secret
#
# The value is read from a file path or from stdin and is never accepted as a
# command-line argument, because argv is world-readable in /proc. It is also
# never passed to the AWS CLI in argv: the request document goes over a pipe.
#
# Prints one line: "<parameter name> <version>". Nothing else reaches stdout.
set -euo pipefail

PREFIX="${GATEWAY_SSM_PREFIX:-/mcp-gateway/demo/}"

usage() {
  cat >&2 <<'EOF'
usage: put_parameter.sh <name> [value-file]

  <name>       parameter name; a bare name is placed under the demo prefix
               (github_client_id, github_client_secret, operator_github_ids,
                viewer_github_ids, gateway_domain)
  [value-file] file holding the value; omit to read the value from stdin

Environment:
  GATEWAY_SSM_PREFIX  parameter prefix (default /mcp-gateway/demo/)
EOF
  exit 2
}

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  usage
fi
command -v aws >/dev/null 2>&1 || {
  echo "aws CLI not found" >&2
  exit 5
}

name="$1"
case "$name" in
/*) full="$name" ;;
*) full="${PREFIX%/}/${name}" ;;
esac

source_file="${2:-/dev/stdin}"
[ -r "$source_file" ] || {
  echo "cannot read value source: $source_file" >&2
  exit 2
}

# Build the request document in a private temp file (0600, removed on exit)
# and hand it to the CLI by path: the value never appears in argv, and the CLI
# does not read reliably from /dev/stdin. A trailing newline from an editor is
# stripped; anything else is preserved verbatim.
request="$(mktemp)"
chmod 0600 "$request"
trap 'rm -f "$request"' EXIT
python3 -c '
import json, sys
name = sys.argv[1]
with open(sys.argv[2], "rb") as handle:
    raw = handle.read()
value = raw.decode("utf-8").rstrip("\n")
if not value:
    sys.stderr.write("refusing to store an empty value\n")
    sys.exit(2)
with open(sys.argv[3], "w", encoding="utf-8") as out:
    json.dump({"Name": name, "Value": value, "Type": "SecureString",
               "Overwrite": True}, out)
' "$full" "$source_file" "$request"
version="$(aws ssm put-parameter --cli-input-json "file://${request}" --output json |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["Version"])')"

printf '%s %s\n' "$full" "$version"
