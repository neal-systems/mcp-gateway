# Technical defense guide

What the author must be able to explain about this deployment, one decision at a time. Written against the code and configuration in this repository; anything not in the repository is marked as such.

## 1. Single EC2 Host with Docker Compose Instead of ECS or Kubernetes

The gateway runs on a single EC2 host with Docker Compose and Caddy rather than a container cluster orchestrator.

Dedicated local filesystem state for Caddy certificates and OAuth registrations is simpler and cheaper on one node, eliminating control plane fees, NAT gateways, and distributed volume complexity.

Amazon ECS or EKS was rejected because, in practice, a managed orchestrator for an internet-facing service brings a load balancer, private subnets with a NAT gateway, and cluster configuration that would cost more per month than the service itself and would prove nothing extra for a single-tenant tool.

**Follow-up Question:** How do you recover from host failure without automated cluster orchestration?

**Answer:** Host recovery is a Terraform apply that launches a replacement instance and re-attaches the separate state volume, followed by a normal release. There is no automated failover; that is a documented limitation, not a hidden one.

## 2. Generate-Once Signing Key Persisted on an Encrypted Volume

The gateway generates a 32-byte JWT signing key on first boot, persists it to an encrypted EBS volume with 0600 permissions, and reuses it on subsequent restarts unless an explicit key is supplied.

This avoids manual key provisioning, and a restart reuses the same key, so tokens issued before the restart stay verifiable for the provider's documented lifetime. Sessions are not promised to live forever.

Supplying an external secret requires manual operational steps, while rotating keys on restart terminates active user sessions. Previously, a placeholder key caused startup failure because validation treated the unset key as an error before reaching generation logic, creating dead code until fixed.

**Follow-up Question:** How does the application avoid race conditions during key generation?

**Answer:** The application writes the key to a temporary file with 0600 permissions and atomically replaces the destination path. Release operations are also serialized under an exclusive file lock.

## 3. Separate Liveness and Readiness Endpoints Independent of GitHub

The server exposes an unauthenticated /healthz endpoint for process liveness and /readyz for readiness, keeping liveness completely independent of external GitHub availability.

Liveness indicates only whether the local HTTP server process is running, preventing destructive container restarts during external network incidents.

A single combined health check was rejected because external network blips or GitHub degradation would trigger repeated container restarts without resolving upstream problems.

**Follow-up Question:** What conditions cause the readiness check to return HTTP 503?

**Answer:** Readiness fails if sample data is unreadable, the state directory is unwritable, the signing key is missing, auth is unconfigured, or fault injection is enabled. It never makes blocking network calls to GitHub.

## 4. Dual-Layer Tool Authorization and Outermost Telemetry Span

Tool permissions are filtered during catalog discovery and independently checked upon direct invocation, with the telemetry span middleware wrapped outside the authorization layer.

Catalog filtering prevents exposing unauthorized tools, while direct checks stop clients from calling unlisted tools by guessing names. Wrapping telemetry outside ensures denied requests are recorded as authorization denials.

Filtering discovery alone leaves direct calls unprotected, while placing telemetry inside authorization prevents denied calls from being recorded because exceptions abort execution before inner spans start.

**Follow-up Question:** Why are tool arguments excluded from telemetry spans and logs?

**Answer:** Tool arguments may contain private user prompts or returned data that must not enter logging streams. The telemetry contract strictly limits attributes to tool names, coarse outcomes, duration, and correlation IDs.

## 5. Secrets in SSM Parameter Store Read at Release Time

Runtime credentials are stored as encrypted SecureString parameters in SSM Parameter Store and fetched directly on the instance at release time, never through Terraform variables, user-data, or workflow logs.

Fetching secrets on the host at release time ensures sensitive values are never committed to git, stored in plaintext Terraform state, or exposed in CI logs.

Passing secrets via Terraform variables or user-data was rejected because it leaks secrets into S3 state backends, EC2 console logs, and GitHub Actions step summaries.

**Follow-up Question:** What IAM permissions allow the instance to read these parameters securely?

**Answer:** The instance role grants ssm:GetParametersByPath limited to the /mcp-gateway/demo/ prefix and kms:Decrypt on the SSM key. A ViaService condition restricts key decryption exclusively to SSM requests within the region.

## 6. GitHub Actions OIDC Bound to Environment Without Ref Conditions

GitHub Actions assumes the AWS deployment role using OpenID Connect authenticated against the subject repo:neal-systems/mcp-gateway:environment:demo without combining it with a ref condition.

GitHub Actions environment-protected jobs emit an OIDC subject formatted as repo:<org>/<repo>:environment:<env>, enabling secure authorization to demo without long-lived AWS credentials.

Static IAM keys pose leakage risks, while combining the environment subject with a branch ref condition creates an unsatisfiable trust policy: a job bound to an environment presents the environment-shaped subject claim, not the ref-shaped one, so both conditions can never be true at once.

**Follow-up Question:** How are unauthorized deployments prevented without a branch condition in the trust policy?

**Answer:** Deployment authority is restricted by GitHub environment protection rules governing which branches and users may deploy to demo. A concurrency group serializes runs to prevent race conditions.

## 7. Digest-Pinned Release Manifest and Health-Gated Cutover

Releases are built once in CI, published by immutable sha256 digest in a versioned release manifest, and deployed with a health-gated cutover that measures a brief service interruption.

Deploying by digest prevents tag drift and guarantees reproducibility, while measuring brief container swap downtime reflects single-node reality honestly without claiming zero downtime.

Deploying mutable tags like latest risks non-deterministic rollbacks, while complex zero-downtime proxy socket swaps on a single host introduce hidden failure modes and dropped packets.

**Follow-up Question:** What occurs if a new release fails its readiness probe?

**Answer:** The release script waits up to 60 seconds for readiness; if the candidate fails, it restarts the retained known-good manifest and exits with code 3. Both the failure and measured interruption duration are logged in releases.log.

## 8. AWS Systems Manager Run Command Instead of SSH

Remote instance configuration, deployments, and operational drills are executed through AWS Systems Manager Run Command rather than opening SSH access.

SSM Run Command eliminates inbound port 22 exposure, removes SSH key management, and provides IAM authorization and CloudTrail audit logging.

Opening SSH was rejected because internet-facing SSH ports invite brute-force scans, require managing private keys on CI runners, and lack native integration with AWS IAM policies.

**Follow-up Question:** How does IAM limit what actions GitHub Actions can execute via SSM?

**Answer:** The deployment IAM policy restricts ssm:SendCommand to AWS-RunShellScript and targets only instances tagged Project=mcp-gateway-demo. The workflow also validates release identifiers and digests before execution.

## 9. Bounded Telemetry Path with In-Process Redaction and Sentinels

Application logs and OpenTelemetry spans and metrics stream as structured JSON lines to standard error with Docker log rotation and trace sampling, validated by tests using synthetic sentinel secrets.

Streaming bounded JSON to standard error prevents host disk exhaustion without external collectors, while automated tests verify that sensitive tokens are scrubbed before export.

An OpenTelemetry collector sidecar was rejected because it is a second process to size, patch, and secure on one small host, and it adds nothing the demo needs; unrotated disk logging was rejected because it risks filling the host disk.

**Follow-up Question:** How does the redaction filter prevent deferred string formatting from leaking secrets?

**Answer:** The RedactingFilter scrubs the template, argument variables, and custom attributes in place before message formatting. Matched sensitive patterns and known secret shapes are replaced with a redacted marker before JSON output.

## 10. Native Amazon S3 State Locking Instead of DynamoDB

Terraform state locking uses native S3 locking with use_lockfile = true instead of provisioning an auxiliary DynamoDB table.

Modern Terraform supports native conditional writes in S3 for state locking, eliminating the cost, configuration boilerplate, and maintenance of an extra DynamoDB table.

A DynamoDB table with a LockID key was rejected because it introduces unnecessary cloud resources and permissions without providing any benefit over native S3 locking.

**Follow-up Question:** How is the Terraform state bucket protected against corruption or data loss?

**Answer:** The bucket enforces versioning, SSE-S3 (AES256) encryption, public access blocking, and a bucket policy that denies non-TLS requests. A lifecycle rule expires noncurrent state versions after a configured number of days and aborts incomplete multipart uploads after seven.

## 11. Distinct Evidence Classes in Verification Manifests

The verification framework groups checks into strict, separate evidence classes (unit, container, ci, cloud, oauth) and strictly prohibits collapsing them into a single summary status.

Each evidence class verifies distinct trust domains, ranging from offline Python logic to container behavior, CI workflows, live AWS infrastructure, and external OAuth handshakes.

Collapsing checks into an aggregate pass status or coverage percentage was rejected because high-level summaries obscure environment-specific failures, such as unit tests passing while cloud deployment fails.

**Follow-up Question:** What is the purpose of the negative control check unit.negative_control_authz?

**Answer:** The negative control mutates authorization logic in a temporary copy of the code to ensure the test suite actively fails when security checks are disabled. A test suite that passes with security checks stripped proves nothing.
