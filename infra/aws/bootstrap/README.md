# Bootstrap

Run once per AWS account, locally, by the owner. It creates the things the demo
environment needs before it can have any state of its own.

## What it creates

- **S3 bucket `mcpgw-demo-tfstate-<account_id>`** for the demo environment's
  Terraform state: versioned, SSE-S3 encrypted, all four public-access blocks
  on, a bucket policy denying any non-TLS request, and a lifecycle rule that
  expires noncurrent versions after 30 days. There is no DynamoDB lock table:
  Terraform 1.10's `use_lockfile` does locking in S3 itself.
- **GitHub Actions OIDC provider** for `token.actions.githubusercontent.com`
  with audience `sts.amazonaws.com`. Creation is conditional on
  `create_oidc_provider` (default `true`). An account may hold only one provider
  per URL, so set it to `false` in an account that already has one and the
  configuration looks the existing provider up instead. **Reusing a shared
  provider means every repository in the account can present tokens to it**; the
  role's trust policy is then the only boundary. Confirm you are authorised to
  attach a role to a provider you do not own.
- **IAM role `mcpgw-demo-github-deploy`**, assumable only by a job whose OIDC
  subject is exactly `repo:neal-systems@324300420/mcp-gateway@1355437034:environment:demo`
  (the immutable form GitHub issues for repositories created after 2026-07-15;
  set `github_subject_format = "legacy"` for an older repository), with a
  one-hour maximum session. It may send `AWS-RunShellScript` to instances tagged
  `Project=mcp-gateway-demo`, read the results, and describe instances. Nothing
  else.

State is local (`terraform.tfstate` next to the configuration) because this is
what creates the remote state bucket. Keep the file; it is gitignored.

## Cost

Effectively nothing. An empty state bucket holding a few hundred KB of
versioned objects is well under USD 0.05/month; IAM roles and OIDC providers are
free. There is no compute here.

## How to run

```bash
cd infra/aws/bootstrap
terraform init
terraform apply
terraform output
```

Record the `region` and `state_bucket` outputs: they are what
`infra/aws/demo/backend.hcl` needs. Give `deploy_role_arn` to the GitHub
Actions workflow.

## How to destroy

```bash
terraform destroy
```

The bucket must be empty first, which means destroying the demo environment
first (`cd ../demo && terraform destroy`) and then deleting the remaining state
objects and their noncurrent versions.

**The state bucket and the OIDC provider are meant to outlive a demo teardown.**
Tearing the demo down and standing it back up should reuse both; destroying
bootstrap as well throws away the state history and forces the OIDC provider and
role to be recreated, which means updating the role ARN in the workflow. Destroy
bootstrap only when you are finished with the account.
