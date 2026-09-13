# Operator runbook: AWS single-node deployment

Status: every step below was executed once end to end on 2026-09-13 (bootstrap, provisioning, releases A and B, the failed-candidate drill, rollback, restart and reboot, live OAuth, evidence collection, teardown) and is recorded in docs/cloud/EVIDENCE.md. Steps that bit on the first live run are marked in Troubleshooting.

This runbook documents the deployment and operation of the read-only Model Context Protocol (MCP) gateway on Amazon Web Services (AWS) using a reproducible single-node architecture. The deployment runs Docker Compose and Caddy on an Amazon Linux 2023 EC2 host, administered remotely via AWS Systems Manager (SSM) Run Command.

## Prerequisites

### Tools and Versions

Tool versions match docs/cloud/VERSIONS.md:

- Terraform: version 1.16.2. S3 native state locking requires Terraform 1.10 or later.
- Docker and Docker Compose: Docker Engine with Compose plugin v5.5.1 (SHA256 db1889184726840f75c4f9c001048430d4f25b3be3cb084d3ddd762bc0aed576).
- Python: version 3.12, matching the app base image.
- AWS CLI: version 2, configured with owner permissions.
- GitHub CLI (gh): authenticated for repository and GHCR queries.
- curl: supporting HTTP/1.1 and TLS with --fail-with-body.
- util-linux flock: used for release locking.
- Validation tools: actionlint 1.7.12, shellcheck 0.11.0, hadolint 2.15.1, gitleaks 8.30.1.

### AWS Credentials

AWS credentials are required for the repository owner only. All Terraform provisioning, secret updates, and teardowns execute locally from the owner workstation. CI runners never hold long-lived AWS keys, and Terraform apply is never run from GitHub Actions.

### GitHub OAuth App

Create a GitHub OAuth App at https://github.com/settings/developers (OAuth Apps, New OAuth App):

- Homepage URL: https://<demo_hostname>
- Authorization callback URL: https://<demo_hostname>/auth/callback

Record the Client ID and Client Secret. When using derived sslip.io hostnames, update the callback URL after the Elastic IP is allocated.

### Operator Identity

Retrieve the operator numeric GitHub ID:

```bash
gh api user --jq .id
```

The gateway authorization logic requires numeric IDs. Usernames are rejected. Startup fails closed if operator IDs are missing, non-numeric, or assigned across multiple roles.

## Bootstrap once

Bootstrap provisions shared account resources that outlive demo instances. Execute this once per AWS account from your local workstation.

### Commands

```bash
cd infra/aws/bootstrap
terraform init
terraform apply
terraform output
```

### Outputs and Configuration

Bootstrap creates the S3 state bucket mcpgw-demo-tfstate-<account_id> with SSE-S3 encryption and S3 native locking (use_lockfile), the GitHub Actions OIDC provider (token.actions.githubusercontent.com), and the IAM deploy role (mcpgw-demo-github-deploy).

Propagate outputs:

1. Copy state_bucket and region into infra/aws/demo/backend.hcl:
   ```bash
   cp infra/aws/demo/backend.hcl.example infra/aws/demo/backend.hcl
   ```
   Set bucket = "<state_bucket>" and region = "<region>".
2. In GitHub repository settings, create environment "demo". Set variables:
   - AWS_DEPLOY_ROLE_ARN: the deploy_role_arn output.
   - AWS_REGION: the region output.

## Put secrets

Application secrets live in AWS SSM Parameter Store as SecureString parameters under /mcp-gateway/demo/. Secrets are never placed in Terraform code, state files, or git.

### Usage and Commands

Store parameters using scripts/cloud/put_parameter.sh:

```bash
scripts/cloud/put_parameter.sh gateway_domain ./domain.txt
scripts/cloud/put_parameter.sh github_client_id ./client-id.txt
scripts/cloud/put_parameter.sh github_client_secret ./client-secret.txt
scripts/cloud/put_parameter.sh operator_github_ids ./operator-ids.txt
scripts/cloud/put_parameter.sh viewer_github_ids ./viewer-ids.txt
```

Values may also be piped on stdin:

```bash
printf '%s' "111111111" | scripts/cloud/put_parameter.sh operator_github_ids
printf '%s' "" | scripts/cloud/put_parameter.sh viewer_github_ids
```

Delete temporary secret files immediately after writing.

### Input Security and Ordering Trap

Values must come from a file or stdin only, never from command-line arguments (argv is world-readable in /proc).

Ordering trap: SSM parameters must exist before the first demo apply. The default KMS alias alias/aws/ssm is only created when the first SecureString parameter is written. The demo IAM policy grants kms:Decrypt on alias/aws/ssm; running terraform apply beforehand causes KMS alias resolution to fail. The instance itself boots without them (cloud-init only installs Docker, mounts the state volume, and installs the scripts); it is the first `gateway-release deploy` that renders /etc/mcp-gateway/config.env from the parameters and exits 5 if they cannot be read.

## Provision

Provisioning builds the demo host, network, and encrypted state volume. This step runs locally by the repository owner.

### Commands and Outputs

```bash
cd infra/aws/demo
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
terraform output
```

Outputs:

- instance_id: Target EC2 instance identifier for SSM commands.
- public_ip: Allocated Elastic IP attached to the host.
- demo_hostname: Public hostname, derived from the Elastic IP via sslip.io (e.g. 198-51-100-1.sslip.io) if var.demo_hostname is empty.
- state_volume_id: Dedicated 4 GiB encrypted gp3 EBS volume mounted at /srv/mcp-gateway.
- instance_role_arn: Role mcpgw-demo-instance assumed by the host.

The instance has no SSH access: port 22 is closed and no key pair exists. Ingress ports 80 and 443 are open to 0.0.0.0/0 to allow ACME HTTP-01 challenges and OAuth redirects.

### Updating Callback and Domain

Update the GitHub OAuth App callback URL to https://<demo_hostname>/auth/callback. If the hostname was derived during apply, update gateway_domain in SSM Parameter Store:

```bash
printf '%s' "<demo_hostname>" | scripts/cloud/put_parameter.sh gateway_domain
```

## Release

Releases follow an immutable build-once model. Images are built in GitHub Actions, published to GHCR by digest, and deployed without SSH.

### Build and Publish (release.yml)

Triggered by pushing tag v* or dispatching .github/workflows/release.yml:

1. Builds container image ./app for linux/amd64.
2. Pushes image to ghcr.io/neal-systems/mcp-gateway.
3. Derives release_id as sha-<12 hex digits> from commit SHA.
4. Captures image_digest as sha256:<64 hex digits> from docker build-push-action.
5. Emits release-manifest.json artifact and summary. Latest tags are never used.

### First publish: make the package public

GHCR creates a container package as private the first time a workflow pushes
it, even from a public repository. The demo host pulls anonymously on
purpose (no registry credential lives on the instance), so the very first
deploy fails at `docker pull` with "unauthorized" until the package is made
public once, by hand, at
https://github.com/orgs/neal-systems/packages/container/mcp-gateway/settings
(Danger Zone, Change package visibility, Public). GitHub has no API for this
switch. It stays public for every later push. Observed on the first live
run: deploy run 34774294877, exit 5.

### Deployment Workflow (deploy.yml)

Triggered by workflow_dispatch on .github/workflows/deploy.yml:

- release_id: sha-<12 hex> identifier.
- image_digest: sha256:<64 hex> digest.
- action: deploy (default), rollback, drill-not-ready, smoke, status, evidence.

Runs in environment "demo", assumes AWS_DEPLOY_ROLE_ARN via OIDC, resolves the instance tagged Project=mcp-gateway-demo and Name=mcpgw-demo, and invokes /opt/mcp-gateway/bin/gateway-release over SSM Run Command. Concurrency group deploy-demo serializes runs.

### Local Deployment Script

Run equivalent local deployments using scripts/cloud/ops/deploy.sh:

```bash
scripts/cloud/ops/deploy.sh \
  --release-id sha-a1b2c3d4e5f6 \
  --image-digest sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  --region us-east-2
```

## Verify

Verification includes automated HTTPS checks, interactive browser authentication, and SSM status queries.

### Automated Smoke Verification

Run external HTTPS checks:

```bash
scripts/cloud/ops/smoke.sh --hostname <demo_hostname> --region us-east-2
```

Verifies GET /healthz is 200, GET /readyz is 200, unauthenticated POST /mcp is 401 with WWW-Authenticate header, OAuth metadata discovery is 200, and TLS handshake succeeds.

### Browser authentication login

The gateway is an OAuth-protected MCP server; the login is started by an MCP client, not by browsing to a page. Configure any MCP client that supports remote HTTP servers with dynamic client registration (for example the `fastmcp` Python client, or a chat client's remote-MCP settings) with the URL https://<demo_hostname>/mcp. The client registers itself, the gateway redirects the browser to GitHub, GitHub returns to https://<demo_hostname>/auth/callback, and the gateway issues its own token.

Expected outcomes:

- A GitHub account whose numeric id is in operator_github_ids sees three tools and can call search_runbooks.
- A GitHub account whose numeric id is in viewer_github_ids sees two tools; a direct call to search_runbooks is refused.
- Any other GitHub account completes the GitHub login but the gateway rejects the identity; the client receives 401 and no tools.
- With no token at all, POST /mcp returns 401 with a WWW-Authenticate header (checked by smoke.sh).

Registrations and the signing key are stored under /data/state on the encrypted state volume.

### Status Query

Query release status via SSM:

```bash
scripts/cloud/ops/status.sh --region us-east-2
```

Returns JSON with release_id, image, ready, and known-good metadata.

## Upgrade A to B with interruption_probe.sh running alongside

Single-node upgrades swap containers, causing a brief interruption. Run interruption_probe.sh alongside deploy.sh to measure downtime.

1. Launch probe in one terminal:
   ```bash
   scripts/cloud/ops/interruption_probe.sh --hostname <demo_hostname> --seconds 60 --region us-east-2
   ```
   Polls https://<demo_hostname>/readyz every 0.5 seconds.
2. Dispatch deployment of Release B in another terminal:
   ```bash
   scripts/cloud/ops/deploy.sh --release-id <release_b_id> --image-digest <release_b_digest> --region us-east-2
   ```
3. Review probe JSON output: total_samples, non_200_samples, and longest_consecutive_non_200_gap_seconds.
4. Check /opt/mcp-gateway/releases.log where gateway-release records the exact interruption_seconds between stopping the old container and candidate readiness.

## Deliberate failure drill

Test automated rollback by deploying a candidate configured to fail readiness.

### Execution and Rollback Mechanism

Trigger via deploy.yml with action drill-not-ready, or run locally:

```bash
scripts/cloud/ops/deploy.sh \
  --release-id <release_id> \
  --image-digest <image_digest> \
  --drill-not-ready \
  --region us-east-2
```

gateway-release renders a compose override (override.yml) containing GATEWAY_FAULT_INJECT: not_ready. This flag is never written to /etc/mcp-gateway/config.env. The candidate starts, but /readyz returns 503. After 60 seconds (GATEWAY_READY_TIMEOUT), gateway-release stops the candidate, restores the known-good release from /opt/mcp-gateway/known-good.json, verifies its readiness, and exits with code 3. In deploy.yml, SSM status Failed with response code 3 is treated as workflow success.

### Reading releases.log

Inspect /opt/mcp-gateway/releases.log. It records the failed deployment followed immediately by automated rollback:

```json
{"action": "deploy", "at": "2026-09-13T12:00:00Z", "image": "ghcr.io/neal-systems/mcp-gateway@sha256:...", "interruption_seconds": null, "outcome": "not_ready", "release_id": "sha-drill", "seconds_to_ready": null}
{"action": "rollback", "at": "2026-09-13T12:01:05Z", "image": "ghcr.io/neal-systems/mcp-gateway@sha256:...", "interruption_seconds": 3.45, "outcome": "ready", "release_id": "sha-good", "seconds_to_ready": 2.15}
```

## Rollback

To manually revert to the known-good release, run scripts/cloud/ops/rollback.sh or select rollback in deploy.yml.

```bash
scripts/cloud/ops/rollback.sh --region us-east-2
```

gateway-release acquires /opt/mcp-gateway/lock, reads known-good.json (exits 3 if missing), re-renders compose configuration, pulls the image, stops the current container, brings up the known-good release, waits up to 60 seconds for /readyz 200, updates the current symlink, and logs the rollback to releases.log.

## Restart and reboot checks

Verify state persistence across restarts.

### Container Restart Check

Restart the container via SSM:

```bash
aws ssm send-command \
  --instance-ids <instance_id> \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["docker restart mcpgw-mcp-app-1"]' \
  --region us-east-2
```

Verify that /data/state/jwt_signing_key maintains its byte-identical checksum and client_storage database remains intact.

### Host Reboot Check

Reboot the EC2 instance:

```bash
aws ec2 reboot-instances --instance-ids <instance_id> --region us-east-2
```

On boot, systemd service /etc/systemd/system/mcp-gateway.service executes /opt/mcp-gateway/bin/gateway-release resume. Resume inspects /opt/mcp-gateway/current and starts active containers without altering state.

Verify health post-reboot:

```bash
scripts/cloud/ops/smoke.sh --hostname <demo_hostname> --region us-east-2
scripts/cloud/ops/status.sh --region us-east-2
```

## Evidence collection

Collect diagnostic evidence bundles using scripts/cloud/ops/evidence.sh.

```bash
scripts/cloud/ops/evidence.sh --out evidence/raw/bundle-$(date +%Y%m%d) --region us-east-2
```

Saves four JSON files to --out:

- evidence-remote-<timestamp>.json: gateway-release evidence output (status, last 20 releases.log entries, last 200 lines of app logs).
- describe-instances-<timestamp>.json: EC2 instances tagged Project=mcp-gateway-demo.
- describe-volumes-<timestamp>.json: EBS volumes tagged Project=mcp-gateway-demo.
- describe-addresses-<timestamp>.json: Elastic IPs tagged Project=mcp-gateway-demo.

Raw evidence is saved under evidence/raw/, which is gitignored and stays private. gateway-release scrubs GitHub tokens (gh[pousr]_...), Bearer tokens, and 64-character hex strings before output. Tracked evidence/acceptance.json records check status without secrets.

## Teardown

Tear down demo resources using scripts/cloud/ops/teardown.sh.

```bash
scripts/cloud/ops/teardown.sh --yes --region us-east-2
```

Prints resource inventory, requires --yes, and executes terraform destroy in infra/aws/demo.

### Destroyed and Retained Resources

Teardown destroys the EC2 instance, Elastic IP, VPC, and dedicated EBS state volume. The generated JWT signing key and client OAuth registrations are discarded on teardown by design.

The following resources remain:

- Bootstrap S3 state bucket: mcpgw-demo-tfstate-<account_id>.
- GitHub Actions OIDC provider: token.actions.githubusercontent.com.
- IAM deploy role: mcpgw-demo-github-deploy.
- SSM Parameter Store parameters: under /mcp-gateway/demo/ unless --delete-parameters is specified.
- GHCR container images: stored on GitHub Packages.

### Residual Monthly Costs

Residual monthly costs are effectively zero:

- S3 state storage: small state files cost well under USD 0.05 per month.
- IAM role and OIDC provider: zero cost.
- SSM parameters: Standard tier is free.
- GHCR packages: free for public repositories.

### Complete Account Cleanup

1. Delete SSM parameters:
   ```bash
   scripts/cloud/ops/teardown.sh --yes --delete-parameters --region us-east-2
   ```
2. Delete GHCR packages via GitHub API or web interface.
3. Empty all object versions from the S3 state bucket.
4. Destroy bootstrap infrastructure:
   ```bash
   cd infra/aws/bootstrap
   terraform destroy
   ```

## Exit codes table for gateway-release

Per docs/cloud/CONTRACTS.md section 5:

| Exit Code | Name | Meaning |
|---|---|---|
| 0 | OK | Success. Requested operation completed cleanly. |
| 2 | Validation Error | Usage error, invalid base64 manifest, JSON schema mismatch, missing required fields, or regex validation failure on release_id or image. |
| 3 | Release Failed | Candidate failed readiness probe within 60 seconds and rolled back, or rollback failed, or no known-good release recorded. |
| 4 | Lock Held | Another process holds the exclusive lock on /opt/mcp-gateway/lock. |
| 5 | Prerequisite Missing | Required binary missing (aws, docker, python3, curl), docker pull failed, SSM parameters unreadable, or gateway_domain missing. |

## Troubleshooting

### Lock Held (Exit 4)

- Symptom: gateway-release exits with code 4.
- Cause: An active release, rollback, or resume is running, or a previous process died leaving an inherited flock descriptor open on /opt/mcp-gateway/lock.
- Resolution: Check active processes via ps aux | grep gateway-release. Do not terminate flock with SIGKILL. If no process is running, test with flock -n /opt/mcp-gateway/lock true. If locked by an orphaned child process, reboot the host.

### Prerequisite Missing (Exit 5)

- Symptom: Command exits with code 5.
- Cause: Docker daemon stopped, egress to GHCR failed, image digest missing, the GHCR package still private after its first publish (pull says "unauthorized"), or instance IAM role cannot read /mcp-gateway/demo/.
- Resolution: Check Docker with systemctl status docker. Verify image on GHCR with docker manifest inspect. Run scripts/cloud/ops/preflight.sh to check SSM parameters and credentials.

### Candidate Never Ready (Exit 3)

- Symptom: Deployment waits 60 seconds, fails readiness, rolls back, and exits with code 3.
- Cause: Container crashed, configuration missing, unwritable state directory, or fault injection enabled.
- Resolution: Check container state with docker ps -a. View container logs with docker logs mcpgw-mcp-app-1. Check releases.log. gateway-release automatically restores the known-good release.

### Let's Encrypt Rate Limits with sslip.io

- Symptom: Caddy fails to obtain TLS certificate; logs show ACME rate limit errors.
- Cause: sslip.io is a public shared domain subject to Let's Encrypt limits (50 certificates per registered domain per week).
- Resolution: Set var.demo_hostname in infra/aws/demo to a custom domain, point a DNS A record to the Elastic IP, update gateway_domain in SSM, and update the OAuth callback URL in GitHub.

### SSM Agent Not Registered

- Symptom: Instance does not appear in SSM; commands fail to dispatch.
- Cause: amazon-ssm-agent cannot reach AWS SSM endpoints due to missing IAM instance profile, missing default route in public subnet route table, or cloud-init failure.
- Resolution: Verify mcpgw-demo-instance profile is attached with AmazonSSMManagedInstanceCore. Confirm route table routes 0.0.0.0/0 to internet gateway. Review cloud-init console logs in AWS console.
