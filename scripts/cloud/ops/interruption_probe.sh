#!/usr/bin/env bash
# Poll /readyz twice a second for N seconds and measure the interruption,
# for use around an A-to-B release upgrade.
# Usage: interruption_probe.sh --hostname H --seconds N [--region R]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_lib.sh"

ops_parse_region "$@"
set -- "${REMAINING_ARGS[@]}"

hostname=""
seconds=""
while [ $# -gt 0 ]; do
  case "$1" in
    --hostname) hostname="$2"; shift 2 ;;
    --hostname=*) hostname="${1#--hostname=}"; shift ;;
    --seconds) seconds="$2"; shift 2 ;;
    --seconds=*) seconds="${1#--seconds=}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$hostname" ] || [ -z "$seconds" ]; then
  echo "usage: $0 --hostname H --seconds N [--region R]" >&2
  exit 2
fi

ops_require_identity
instance_id=$(ops_find_instance)
ops_require_project_tag "$instance_id"

python3 - "$hostname" "$seconds" <<'PYEOF'
import json
import sys
import time
import urllib.request

hostname, seconds = sys.argv[1], float(sys.argv[2])
url = f"https://{hostname}/readyz"
interval = 0.5
deadline = time.monotonic() + seconds

total = 0
non_200 = 0
gap_start = None
longest_gap = 0.0

while time.monotonic() < deadline:
    tick = time.monotonic()
    ok = False
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            ok = resp.status == 200
    except Exception:
        ok = False
    total += 1
    now = time.monotonic()
    if ok:
        if gap_start is not None:
            longest_gap = max(longest_gap, now - gap_start)
            gap_start = None
    else:
        non_200 += 1
        if gap_start is None:
            gap_start = now
    remaining = interval - (time.monotonic() - tick)
    if remaining > 0:
        time.sleep(remaining)

if gap_start is not None:
    longest_gap = max(longest_gap, time.monotonic() - gap_start)

print(json.dumps({
    "hostname": hostname,
    "seconds_requested": seconds,
    "total_samples": total,
    "non_200_samples": non_200,
    "longest_consecutive_non_200_gap_seconds": round(longest_gap, 2),
}, indent=2))
PYEOF
