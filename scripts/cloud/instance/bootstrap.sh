#!/usr/bin/env bash
# bootstrap.sh - one-time (and re-runnable) instance preparation for the
# mcp-gateway demo host on Amazon Linux 2023.
#
# Invoked by cloud-init on first boot and safe to re-run by hand afterwards:
# every step checks for its own result before doing anything.
set -euo pipefail

STATE_ROOT="${GATEWAY_STATE_ROOT:-/srv/mcp-gateway}"
RELEASE_ROOT="${GATEWAY_RELEASE_ROOT:-/opt/mcp-gateway}"
CONFIG_DIR="${GATEWAY_CONFIG_DIR:-/etc/mcp-gateway}"
APP_UID="${GATEWAY_APP_UID:-10001}"

# Pinned Compose plugin. AL2023's repositories carry the docker engine but no
# compose plugin, so it comes from the upstream release with a checksum.
# Refresh: curl -sSL https://github.com/docker/compose/releases/download/<tag>/docker-compose-linux-x86_64.sha256
COMPOSE_VERSION="v5.5.1"
COMPOSE_SHA256="db1889184726840f75c4f9c001048430d4f25b3be3cb084d3ddd762bc0aed576"
COMPOSE_PLUGIN_DIR="/usr/libexec/docker/cli-plugins"

log() { printf '%s bootstrap: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }
die() {
  log "ERROR: $*"
  exit 1
}

# ------------------------------------------------------------------- docker

install_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    log "installing docker"
    dnf install -y docker >&2
  else
    log "docker already installed"
  fi
  systemctl enable --now docker >&2
}

install_compose_plugin() {
  if docker compose version 2>/dev/null | grep -qF "$COMPOSE_VERSION"; then
    log "compose plugin $COMPOSE_VERSION already installed"
    return 0
  fi
  log "installing compose plugin $COMPOSE_VERSION"
  mkdir -p "$COMPOSE_PLUGIN_DIR"
  local tmp
  tmp="$(mktemp)"
  curl -fsSL -o "$tmp" \
    "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" ||
    die "cannot download the compose plugin"
  printf '%s  %s\n' "$COMPOSE_SHA256" "$tmp" | sha256sum -c - >&2 ||
    die "compose plugin checksum mismatch"
  install -m 0755 "$tmp" "${COMPOSE_PLUGIN_DIR}/docker-compose"
  rm -f "$tmp"
  docker compose version >&2
}

# ------------------------------------------------------------- state volume

# The volume is attached as /dev/xvdf. On Nitro instances the kernel names it
# /dev/nvme1n1 and AL2023's udev rules usually create the /dev/sdf and /dev/xvdf
# symlinks; when they do not, fall back to "the one whole disk that is not the
# root disk".
find_state_device() {
  local cand root_src root_disk name dev
  for cand in /dev/xvdf /dev/sdf; do
    if [ -b "$cand" ]; then
      readlink -f "$cand"
      return 0
    fi
  done

  root_src="$(findmnt -no SOURCE / 2>/dev/null || true)"
  root_disk=""
  if [ -n "$root_src" ]; then
    root_disk="$(lsblk -no PKNAME "$root_src" 2>/dev/null | head -n 1 || true)"
    [ -n "$root_disk" ] || root_disk="$(basename "$root_src")"
  fi

  while read -r name; do
    [ -n "$name" ] || continue
    [ "$name" = "$root_disk" ] && continue
    dev="/dev/$name"
    [ -b "$dev" ] || continue
    printf '%s\n' "$dev"
    return 0
  done < <(lsblk -dno NAME,TYPE | awk '$2 == "disk" { print $1 }')

  return 1
}

prepare_state_volume() {
  local dev uuid
  dev="$(find_state_device)" || die "no separate state volume found"
  log "state volume device: $dev"

  if [ -z "$(blkid -o value -s TYPE "$dev" 2>/dev/null || true)" ]; then
    log "no filesystem on $dev; creating ext4"
    mkfs.ext4 -L mcpgw-state "$dev" >&2
  else
    log "filesystem already present on $dev; leaving it alone"
  fi

  uuid="$(blkid -o value -s UUID "$dev")"
  [ -n "$uuid" ] || die "cannot read the filesystem UUID of $dev"

  mkdir -p "$STATE_ROOT"
  if ! grep -q "UUID=${uuid}" /etc/fstab; then
    log "adding the fstab entry for $uuid"
    printf 'UUID=%s %s ext4 defaults,nofail 0 2\n' "$uuid" "$STATE_ROOT" >>/etc/fstab
    systemctl daemon-reload >&2 || true
  fi

  mountpoint -q "$STATE_ROOT" || mount "$STATE_ROOT" >&2
  mountpoint -q "$STATE_ROOT" || die "$STATE_ROOT did not mount"
}

# ------------------------------------------------------------- directories

prepare_directories() {
  install -d -m 0755 "$STATE_ROOT"
  # uid 10001 is the unprivileged app user inside the image; the bind mount
  # carries host ownership, so it has to match numerically.
  install -d -m 0700 -o "$APP_UID" -g "$APP_UID" "$STATE_ROOT/state"
  install -d -m 0755 "$STATE_ROOT/caddy"
  install -d -m 0755 "$STATE_ROOT/caddy/data" "$STATE_ROOT/caddy/config"
  install -d -m 0700 "$CONFIG_DIR"
  install -d -m 0755 "$RELEASE_ROOT"
  install -d -m 0755 "$RELEASE_ROOT/bin" "$RELEASE_ROOT/releases"
}

# The instance needs its own Caddyfile: the repository copy is not checked out
# here. Keep it byte-equivalent in behaviour to caddy/Caddyfile.
write_caddyfile() {
  local target="$CONFIG_DIR/Caddyfile"
  cat >"$target" <<'EOF'
{
	admin off
}

{$GATEWAY_DOMAIN:gateway.example.com} {
	encode zstd gzip

	header {
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "no-referrer"
		-Server
	}

	reverse_proxy mcp-app:8080
}
EOF
  chmod 0644 "$target"
}

# ---------------------------------------------------------------- systemd

install_unit() {
  cat >/etc/systemd/system/mcp-gateway.service <<EOF
[Unit]
Description=Bring the current mcp-gateway release back up after boot
Documentation=https://github.com/neal-systems/mcp-gateway
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
RequiresMountsFor=${STATE_ROOT}

[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=-${CONFIG_DIR}/instance.env
ExecStart=${RELEASE_ROOT}/bin/gateway-release resume
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload >&2
  systemctl enable mcp-gateway.service >&2
}

main() {
  [ "$(id -u)" -eq 0 ] || die "bootstrap.sh must run as root"
  install_docker
  install_compose_plugin
  prepare_state_volume
  prepare_directories
  write_caddyfile
  chmod 0755 "$RELEASE_ROOT/bin/gateway-release" 2>/dev/null || true
  install_unit
  log "bootstrap complete"
}

main "$@"
