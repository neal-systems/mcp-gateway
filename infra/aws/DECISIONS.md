# Infrastructure decisions

One line each: what, why, cost impact.

- **S3 native locking (`use_lockfile`) instead of a DynamoDB lock table** --
  Terraform >= 1.10 does it in S3; one less resource to create and explain.
  Saves the table (~USD 0 on-demand, but real operational surface).
- **SSE-S3 (AES256) on the state bucket, not SSE-KMS** -- state is encrypted at
  rest either way and SSE-S3 needs no key policy for local applies. Saves the
  CMK (USD 1/month) and per-request KMS charges.
- **No thumbprint pinned on the GitHub OIDC provider** -- IAM validates
  `token.actions.githubusercontent.com` against its own trusted CA set; a pinned
  thumbprint is a rotation outage waiting to happen. No cost impact.
- **Immutable OIDC subject `repo:<owner>@<owner_id>/<repo>@<repo_id>:environment:demo`**
  -- GitHub issues it for repositories created after 2026-07-15 (this one:
  2026-09-03); the name-only form would never match. Found by review; the
  deploy workflow prints the live subject before assuming the role. No cost
  impact.
- **Trust policy conditions on `aud` and `sub` only, with no `ref:` condition**
  -- a job bound to a GitHub environment presents the environment-shaped subject
  and not a ref-shaped one, so a `ref:` condition alongside it would make the
  policy unsatisfiable. No cost impact.
- **`ssm:SendCommand` split across two statements** -- the document and the
  instances are separate resource types, and the `ssm:resourceTag/Project`
  condition can only ever be satisfied by the instance, so one combined
  statement would deny the document. No cost impact.
- **`ssm:GetCommandInvocation` on `*`, no `ListCommandInvocations`** -- a command
  id does not exist until the command is sent, so there is nothing to scope to;
  list access was removed after review because it would expose the output of
  commands sent to unrelated instances. No cost impact.
- **`kms:Decrypt` scoped to the `alias/aws/ssm` target key plus a
  `kms:ViaService` condition** -- the alias lookup ties the grant to the real
  key, and the condition keeps it unusable for anything but Parameter Store.
  Costs an ordering constraint: the alias only exists after the first
  SecureString parameter, so parameters must be put before the first apply
  (documented in the demo README).
- **One public subnet, no private subnet, no NAT gateway** -- the only workload
  is a public web endpoint that pulls images outbound. Saves ~USD 32/month plus
  data processing.
- **No ALB** -- Caddy on the instance terminates TLS and gets certificates from
  Let's Encrypt. Saves ~USD 16/month plus LCU charges.
- **Ingress 80/443 open to the internet by default** -- the OAuth redirect comes
  from a user's browser and the ACME HTTP-01 challenge comes from Let's
  Encrypt, so a pinned CIDR breaks both. No cost impact; it is a deliberate
  exposure of two ports on a host with no SSH.
- **No SSH and no key pair; SSM Session Manager only** -- removes the most
  attacked port and the key-distribution problem. No cost impact.
- **AMI resolved from the public SSM parameter, not pinned** -- keeps the AMI id
  out of git and out of plan review. Costs determinism: a new instance may come
  up on a newer AL2023 image. Acceptable because `user_data_replace_on_change`
  is false, so an AMI change does not recycle a running demo.
- **`monitoring = false`** -- detailed CloudWatch monitoring is a per-instance
  charge for a host nobody pages on. Saves ~USD 2/month.
- **IMDSv2 required with `http_put_response_hop_limit = 1`** -- a container on
  the host cannot reach the instance credentials through the metadata service.
  No cost impact.
- **State volume has no `prevent_destroy` and no retain flag** -- a demo must be
  destroyable in one `terraform destroy`, and a lifecycle guard would leave an
  orphaned volume billing quietly. Costs the signing key and client
  registrations on teardown, which is the intended behaviour. Simpler than a
  `retain_state_volume` variable, which was considered and dropped.
- **`stop_instance_before_detaching = true` on the volume attachment** --
  detaching a mounted filesystem from a running instance corrupts it. Costs a
  short outage during teardown, which a demo can afford.
- **`filebase64`-free user data: `templatefile` + `base64gzip(file(...))` per
  script with cloud-init `gz+b64` encoding** -- no `archive_file` provider
  dependency and no build step. Renders to 12,758 bytes, inside the 16 KiB EC2
  limit with ~3.6 KiB of headroom.
- **Compose plugin pinned to a checksummed upstream release, not a package** --
  AL2023's repositories carry the docker engine but no compose plugin. No cost
  impact.
- **Caddy pinned by multi-arch index digest, recorded as a constant in
  `gateway-release`** -- the front door must not move underneath a release; the
  script carries the command to refresh it. No cost impact.
- **Readiness requires the candidate container to be running, not just a 200 on
  the port** -- a published port is torn down asynchronously when a container
  dies, so during a swap the probe could be answered by the release being
  replaced, and a failing release was intermittently recorded as known-good.
  Found by the local drill. No cost impact.
- **The app port is published on `127.0.0.1` only** -- Caddy reaches it over the
  compose network, so the application is never directly reachable from outside.
  No cost impact.
- **`--drill-not-ready` sets the fault flag through a compose override, never
  through `config.env`** -- the env file is rendered from SSM and outlives the
  drill, so a flag written there could survive into a real release. No cost
  impact.
- **`associate_public_ip_address = true` on the instance** -- cloud-init needs
  the internet to install Docker before the Elastic IP association exists
  (there is no NAT). Public IPv4 is billed per address-hour either way; the
  launch address is released when the EIP attaches.
- **bootstrap waits up to ten minutes for the state volume** -- the attachment
  is a separate resource created after the instance; giving up or formatting
  the wrong disk were the alternatives. No cost impact.
- **docker.service drop-in `RequiresMountsFor=/srv/mcp-gateway`** -- Docker
  restarts `unless-stopped` containers itself, and with `nofail` on the mount
  it could have started the app over the root disk under the mount point. A
  missing mount now stops Docker instead. No cost impact.
- **A drill candidate gets `<release_id>-drill`, and a deploy that would
  overwrite the known-good release's directory with a different image is
  refused (exit 2)** -- otherwise a failed candidate under the current id
  left nothing to roll back to. Found by review. No cost impact.
