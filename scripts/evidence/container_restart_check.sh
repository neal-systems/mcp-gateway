#!/usr/bin/env bash
# Start the image with a named volume, capture the generated signing key hash,
# restart the container, and require the same key plus a ready gateway.
set -euo pipefail
IMAGE="${1:?image tag}"
NAME="mcpgw-restart-$$"; VOL="mcpgw-restart-$$"; PORT="${PORT:-18091}"
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; docker volume rm "$VOL" >/dev/null 2>&1 || true; }
trap cleanup EXIT
run_env=(-e GATEWAY_STATE_DIR=/data/state -e GITHUB_CLIENT_ID=localcheck -e GITHUB_CLIENT_SECRET=localcheck-not-a-secret
         -e GATEWAY_OPERATOR_GITHUB_IDS=111111111 -e "GATEWAY_BASE_URL=http://127.0.0.1:$PORT" -e GATEWAY_HOST=0.0.0.0)
docker run -d --name "$NAME" --read-only --tmpfs /tmp --cap-drop ALL -p "127.0.0.1:$PORT:8080" -v "$VOL:/data/state" "${run_env[@]}" "$IMAGE" >/dev/null
wait_ready() { for _ in $(seq 1 40); do curl -sf -m 2 "http://127.0.0.1:$PORT/readyz" >/dev/null && return 0; sleep 1; done; return 1; }
wait_ready || { echo "not ready before restart"; exit 1; }
before="$(docker exec "$NAME" sh -c 'cat /data/state/jwt_signing_key' | sha256sum | cut -c1-16)"
mode="$(docker exec "$NAME" stat -c %a /data/state/jwt_signing_key)"
docker restart "$NAME" >/dev/null
wait_ready || { echo "not ready after restart"; exit 1; }
after="$(docker exec "$NAME" sh -c 'cat /data/state/jwt_signing_key' | sha256sum | cut -c1-16)"
echo "{\"key_hash_before\":\"$before\",\"key_hash_after\":\"$after\",\"key_mode\":\"$mode\",\"ready_after_restart\":true}"
[ "$before" = "$after" ] && [ "$mode" = "600" ]
