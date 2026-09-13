#!/usr/bin/env bash
# Send fake sentinel secrets at the running container and require that no
# sentinel appears in its logs, that every log line is JSON, and that spans
# and metrics were emitted.
set -euo pipefail
IMAGE="${1:?image tag}"
NAME="mcpgw-logcheck-$$"; PORT="${PORT:-18092}"
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$NAME" --tmpfs /tmp -p "127.0.0.1:$PORT:8080" \
  -e GATEWAY_STATE_DIR=/tmp/state -e GITHUB_CLIENT_ID=localcheck -e GITHUB_CLIENT_SECRET=localcheck-not-a-secret \
  -e GATEWAY_OPERATOR_GITHUB_IDS=111111111 -e "GATEWAY_BASE_URL=http://127.0.0.1:$PORT" -e GATEWAY_HOST=0.0.0.0 \
  -e GATEWAY_METRICS_INTERVAL_SECONDS=2 "$IMAGE" >/dev/null
for _ in $(seq 1 40); do curl -sf -m 2 "http://127.0.0.1:$PORT/readyz" >/dev/null && break; sleep 1; done
curl -s -o /dev/null -H "Authorization: Bearer gho_SENTINELSENTINEL1234" -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' "http://127.0.0.1:$PORT/mcp"
curl -s -o /dev/null "http://127.0.0.1:$PORT/token?code=SENTINELCODE9999&client_secret=SENTINELSECRET" || true
curl -s -o /dev/null -H "X-Request-ID: evidence-req-1" "http://127.0.0.1:$PORT/healthz"
sleep 4
logs="$(docker logs "$NAME" 2>&1)"
non_json="$(printf '%s\n' "$logs" | grep -vc '^{' || true)"
sentinels="$(printf '%s\n' "$logs" | grep -c 'SENTINEL' || true)"
spans="$(printf '%s\n' "$logs" | grep -c '"otel": "span"' || true)"
metrics="$(printf '%s\n' "$logs" | grep -c '"otel": "metrics"' || true)"
correlated="$(printf '%s\n' "$logs" | grep -c 'evidence-req-1' || true)"
echo "{\"non_json_lines\":$non_json,\"sentinel_hits\":$sentinels,\"span_lines\":$spans,\"metric_lines\":$metrics,\"request_id_lines\":$correlated}"
[ "$non_json" -eq 0 ] && [ "$sentinels" -eq 0 ] && [ "$spans" -gt 0 ] && [ "$metrics" -gt 0 ] && [ "$correlated" -gt 0 ]
