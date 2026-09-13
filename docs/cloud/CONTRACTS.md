# Interface contracts (frozen for parallel work)

Owner: lead. Change requests go through the lead; workers do not edit this
file. Everything here is a name or a shape, never a secret value.

## 1. Configuration names

Existing (unchanged meaning): `GATEWAY_DOMAIN`, `GATEWAY_BASE_URL`,
`GATEWAY_HOST`, `GATEWAY_PORT`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`,
`GATEWAY_OPERATOR_GITHUB_IDS`, `GATEWAY_VIEWER_GITHUB_IDS`, `GATEWAY_SAMPLE_DATA`.

| Name | Default | Meaning |
|---|---|---|
| `GATEWAY_STATE_DIR` | `/data/state` | Root of persistent state. Must be writable by the app user. |
| `GATEWAY_CLIENT_STORAGE` | `$GATEWAY_STATE_DIR/client_storage` | OAuth client registrations / sessions (DiskStore). Explicit override only. |
| `GATEWAY_JWT_SIGNING_KEY` | unset | If set and not a placeholder: used as the signing key, must be >= 32 characters, else `ConfigError`. If unset: generate-once contract below. |
| `GATEWAY_JWT_SIGNING_KEY_FILE` | `$GATEWAY_STATE_DIR/jwt_signing_key` | Generated once with mode 0600; never overwritten if present and non-empty. |
| `GATEWAY_RELEASE_ID` | `dev` | Release identifier (image digest or git sha) used only as `service.version` in telemetry. |
| `GATEWAY_LOG_FORMAT` | `json` | `json` or `text`. |
| `GATEWAY_LOG_LEVEL` | `INFO` | stdlib level name. |
| `GATEWAY_OTEL_EXPORTER` | `stdout` | `stdout` (JSON lines on stderr), `otlp` (uses standard `OTEL_EXPORTER_OTLP_*` vars), `none`. |
| `GATEWAY_TRACE_SAMPLE_RATIO` | `1.0` | Parent-based trace-id ratio sampler. |
| `GATEWAY_METRICS_INTERVAL_SECONDS` | `60` | Periodic metric export interval. |
| `GATEWAY_FAULT_INJECT` | unset | `not_ready` makes `/readyz` return 503 forever. Used only for the deliberate failed-release drill. Never affects authorization. |

Placeholder detection stays as today: empty or `YOUR_...` values are
placeholders. Placeholder OAuth client id/secret always fail closed. A
placeholder signing key is NOT an error; it selects the generate-once path.

## 2. Health endpoints (served by the app, proxied by Caddy)

- `GET /healthz` liveness: `200 {"status":"ok"}` whenever the process can
  serve HTTP. No upstream calls. Never 503 because GitHub is down.
- `GET /readyz` readiness: `200 {"status":"ready","checks":{...}}` or
  `503 {"status":"not_ready","checks":{...}}`. Checks (all booleans):
  `sample_data`, `state_dir_writable`, `signing_key`, `auth_configured`,
  `fault_injected` (true only when `GATEWAY_FAULT_INJECT=not_ready`).
- No identity, hostname, path, version, or secret material in either body.
  `Cache-Control: no-store` on both. Unauthenticated.

## 3. State layout

Container: `/data/state/` volume -> `client_storage/` (0700) and
`jwt_signing_key` (0600). App runs as uid 10001. Nothing else under `/data`.

Host (AWS): dedicated encrypted EBS volume mounted at `/srv/mcp-gateway`.
`/srv/mcp-gateway/state` (uid 10001, 0700) is bind-mounted to `/data/state`.
`/srv/mcp-gateway/caddy/{data,config}` hold Caddy certificates.
`/etc/mcp-gateway/config.env` root:root 0600, rendered on the instance from
SSM Parameter Store at release time; never in git, never in Terraform.
`/opt/mcp-gateway/` release area: `bin/`, `releases/<release_id>/`
(`compose.yml`, `manifest.json`), `current` symlink, `known-good.json`,
`releases.log` (append-only JSON lines), `lock` (flock).

## 4. Release manifest (`manifest.json`, schema 1)

```json
{"schema": 1, "release_id": "sha-<12 git chars>", "image": "ghcr.io/neal-systems/mcp-gateway@sha256:<64 hex>",
 "git_sha": "<40 hex>", "built_at": "<ISO-8601 UTC>", "builder": "<workflow run URL or 'local'>"}
```

`image` MUST be a digest reference. Tags are for humans only.

## 5. Instance entrypoint

`/opt/mcp-gateway/bin/gateway-release <subcommand>` (bash, installed by
cloud-init from `scripts/cloud/instance/`):

- `deploy --manifest-b64 <base64 manifest.json> [--drill-not-ready]` : takes the
  flock, renders config from SSM, pulls the digest, stops the current app
  container, starts the candidate, waits up to 60 s for `/readyz` 200 via the
  internal port, on success records it as known-good; on failure restarts the
  known-good release and exits 3. `--drill-not-ready` sets
  `GATEWAY_FAULT_INJECT=not_ready` on the candidate only.
- `rollback` : redeploys `known-good.json` (exit 3 if none).
- `status` : prints current release id, image digest, readiness, known-good.
- `smoke` : unauthenticated checks (`/healthz` 200, `/readyz` 200, `/mcp` 401).
- `evidence` : prints a JSON bundle of status, last 20 `releases.log` lines,
  and the last 200 app log lines (already redacted at source).

Exit codes: 0 ok, 2 usage/validation, 3 release failed and rolled back,
4 lock held, 5 prerequisite missing.

## 6. AWS naming, tagging, and trust

Region: chosen at bootstrap, recorded in `infra/aws/bootstrap/outputs` and
`infra/aws/demo/backend.hcl`. Name prefix `mcpgw-demo`. Tags on every
resource: `Project=mcp-gateway-demo`, `Owner=neal-systems`,
`ManagedBy=terraform`, `Environment=demo`.

Secrets live only in SSM Parameter Store SecureString parameters under
`/mcp-gateway/demo/` (`github_client_id`, `github_client_secret`,
`operator_github_ids`, `viewer_github_ids`, `gateway_domain`). Terraform
declares the parameter NAMES as an IAM resource pattern only; values are put
by `scripts/cloud/put_parameter.sh` reading from a local file or stdin.

Roles: `mcpgw-demo-instance` (SSM core, `ssm:GetParameter*` on the prefix,
KMS decrypt via the default `aws/ssm` key, nothing else);
`mcpgw-demo-github-deploy` (OIDC trust for `repo:neal-systems/mcp-gateway:environment:demo`
only; `ssm:SendCommand` limited to document `AWS-RunShellScript` and instances
tagged `Project=mcp-gateway-demo`, `ssm:GetCommandInvocation`,
`ssm:ListCommandInvocations`, `ec2:DescribeInstances`). Bootstrap and
`terraform apply` run locally under the owner's identity, never from CI.

## 7. Image

`ghcr.io/neal-systems/mcp-gateway`, tagged `sha-<12>`; deployment by digest
only. OCI labels: `org.opencontainers.image.revision`, `.source`, `.created`.
Built once per release workflow run on linux/amd64; the instance is x86_64.

## 8. Evidence schema (`evidence/acceptance.json`, schema 1)

```json
{"schema": 1, "checks": [{"id": "unit.regressions", "title": "...",
  "class": "unit|container|ci|cloud|oauth", "status": "PASS|FAIL|BLOCKED|NOT_RUN",
  "started_at": "...", "finished_at": "...", "duration_s": 1.2,
  "git_sha": "...", "image_digest": "sha256:... or null", "environment": "local|github-actions|aws-demo",
  "command": "...", "evidence_path": "evidence/raw/<id>-<ts>.log", "notes": "..."}]}
```

`evidence/raw/` is gitignored (private). `evidence/acceptance.json` is tracked
and must contain no secrets, hostnames of private systems, or identities.
Tool: `scripts/evidence/record.py` (`run`, `mark`, `render`).
