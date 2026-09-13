#!/usr/bin/env bash
# Boot the gateway image, poll health, and check the auth-gated MCP route.
# Usage: scripts/ci/container_smoke.sh [image-tag]
# Runnable in CI (after `docker build`) or locally against any built tag.
set -euo pipefail

IMAGE="${1:-mcp-gateway:smoke}"
PORT="${SMOKE_PORT:-8080}"
CONTAINER_NAME="mcp-gateway-smoke-$$"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Fixed, obviously-non-live values for the smoke container only: no OAuth
# flow is exercised here, this just needs auth "configured" so /readyz is
# healthy and /mcp still requires a real token.
FAKE_CLIENT_ID="ci-fake"
FAKE_CLIENT_SECRET="ci-fake-not-a-secret"

cleanup() {
  docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  echo "building ${IMAGE} from ${REPO_ROOT}/app" >&2
  docker build -t "${IMAGE}" "${REPO_ROOT}/app"
fi

docker run -d --rm --name "${CONTAINER_NAME}" \
  -p "127.0.0.1:${PORT}:8080" \
  -e GATEWAY_STATE_DIR=/tmp/state \
  -e GITHUB_CLIENT_ID="${FAKE_CLIENT_ID}" \
  -e GITHUB_CLIENT_SECRET="${FAKE_CLIENT_SECRET}" \
  -e GATEWAY_OPERATOR_GITHUB_IDS=111111111 \
  -e GATEWAY_BASE_URL="http://127.0.0.1:${PORT}" \
  -e GATEWAY_HOST=0.0.0.0 \
  "${IMAGE}" >/dev/null

healthy=0
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 1
done
if [ "${healthy}" -ne 1 ]; then
  echo "smoke: /healthz did not return 200 within 30s" >&2
  docker logs "${CONTAINER_NAME}" >&2 || true
  exit 1
fi

readyz_status=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/readyz")
if [ "${readyz_status}" != "200" ]; then
  echo "smoke: /readyz expected 200, got ${readyz_status}" >&2
  docker logs "${CONTAINER_NAME}" >&2 || true
  exit 1
fi

mcp_status=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:${PORT}/mcp" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}')
if [ "${mcp_status}" != "401" ]; then
  echo "smoke: POST /mcp expected 401, got ${mcp_status}" >&2
  docker logs "${CONTAINER_NAME}" >&2 || true
  exit 1
fi

echo "smoke ok: healthz=200 readyz=${readyz_status} mcp=${mcp_status}"
