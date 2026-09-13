# Demo environment

One EC2 host in one public subnet, with an Elastic IP, a separate encrypted
state volume, and no load balancer, NAT gateway, or orchestrator.

## Prerequisites

- `infra/aws/bootstrap` applied, and its `state_bucket` and `region` outputs.
- Terraform >= 1.10 (S3 native state locking).
- Local AWS credentials for the owner. Neither this configuration nor bootstrap
  is ever applied from CI.
- The demo secrets already in SSM (see below). The instance cannot render its
  runtime configuration without them, and `alias/aws/ssm` -- which the instance
  policy grants `kms:Decrypt` on -- does not exist in an account until the first
  SecureString parameter is written. **Put the parameters before the first
  apply.**

## Secrets

Nothing secret is a Terraform variable, an output, or a file in this repository.
Values live only in SSM Parameter Store SecureString parameters under
`/mcp-gateway/demo/`, and the instance reads them at release time to render
`/etc/mcp-gateway/config.env` (root-owned, 0600).

Put them with the helper, which reads the value from a file or stdin and never
from a command-line argument:

```bash
scripts/cloud/put_parameter.sh gateway_domain        ./domain.txt
scripts/cloud/put_parameter.sh github_client_id      ./client-id.txt
scripts/cloud/put_parameter.sh github_client_secret  ./client-secret.txt
scripts/cloud/put_parameter.sh operator_github_ids   ./operator-ids.txt
scripts/cloud/put_parameter.sh viewer_github_ids     ./viewer-ids.txt
```

It prints only the parameter name and its new version. Delete the local files
afterwards.

`gateway_domain` must match the hostname the demo is actually served on and the
GitHub OAuth App's callback URL. With no `demo_hostname` set, that is the
`demo_hostname` output -- an sslip.io name derived from the Elastic IP -- so
allocate the IP first (apply), then set the parameter, then deploy a release.

## Apply

```bash
cd infra/aws/demo
cp backend.hcl.example backend.hcl     # fill in the bucket and region
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
terraform output
```

`backend.hcl` is gitignored.

## What you get

`instance_id` is the SSM `SendCommand` target the deploy workflow uses.
`demo_hostname` is where the gateway answers once a release is deployed. The
instance has **no SSH**: port 22 is not open and there is no key pair. Get a
shell with SSM Session Manager (`aws ssm start-session --target <instance_id>`).

Ports 80 and 443 are open to `allowed_ingress_cidrs`, which defaults to the
whole internet. That default is deliberate: GitHub redirects a browser back to
this host and Let's Encrypt has to reach port 80 for the HTTP-01 challenge, so
neither works from a pinned range.

## Teardown

1. `cd infra/aws/demo && terraform destroy`. This removes the instance, the
   Elastic IP, the VPC, **and the state volume** -- the JWT signing key and every
   OAuth client registration go with it. That is intended for a demo; there is
   no `prevent_destroy` guard to work around.
2. Optionally delete the SSM parameters:
   `aws ssm delete-parameters --names $(aws ssm get-parameters-by-path --path /mcp-gateway/demo/ --query 'Parameters[].Name' --output text)`
3. Leave `infra/aws/bootstrap` alone unless you are finished with the account.
   The state bucket and OIDC provider are meant to survive a teardown.
