locals {
  # A workflow job bound to a GitHub environment presents an environment-shaped
  # subject. It does NOT also present a ref-shaped subject, so adding a `ref:`
  # condition alongside this one would make the trust policy unsatisfiable.
  #
  # Repositories created after 2026-07-15 (this one: 2026-09-03) carry the
  # immutable subject with owner and repository ids embedded; the legacy
  # name-only form never matches their tokens. The ids are public, not secret.
  github_owner = split("/", var.github_repo)[0]
  github_name  = split("/", var.github_repo)[1]
  github_subject = (
    var.github_subject_format == "immutable"
    ? "repo:${local.github_owner}@${var.github_owner_id}/${local.github_name}@${var.github_repo_id}:environment:${var.github_environment}"
    : "repo:${var.github_repo}:environment:${var.github_environment}"
  )
}

data "aws_iam_policy_document" "github_deploy_trust" {
  statement {
    sid     = "GitHubActionsOIDC"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_subject]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name                 = "mcpgw-demo-github-deploy"
  description          = "Deploy role assumed by GitHub Actions in the demo environment."
  assume_role_policy   = data.aws_iam_policy_document.github_deploy_trust.json
  max_session_duration = 3600

  tags = {
    Name = "mcpgw-demo-github-deploy"
  }
}

data "aws_iam_policy_document" "github_deploy" {
  # SendCommand is authorised against two resource types at once: the document
  # and the target instances. They need separate statements because the tag
  # condition can only be satisfied by the instance, never by the document.
  statement {
    sid       = "SendCommandDocument"
    effect    = "Allow"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:${data.aws_partition.current.partition}:ssm:*:*:document/AWS-RunShellScript"]
  }

  statement {
    sid       = "SendCommandTaggedInstances"
    effect    = "Allow"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:${data.aws_partition.current.partition}:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:instance/*"]

    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/Project"
      values   = ["mcp-gateway-demo"]
    }
  }

  # A command result is addressed by command id, which does not exist until
  # the command is sent, so this one cannot be resource-scoped. List access
  # is deliberately absent: it would let the role read the output of
  # commands sent to unrelated instances in the account.
  statement {
    sid       = "ReadCommandResults"
    effect    = "Allow"
    actions   = ["ssm:GetCommandInvocation"]
    resources = ["*"]
  }

  statement {
    sid       = "DescribeInstances"
    effect    = "Allow"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "mcpgw-demo-github-deploy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}

data "aws_partition" "current" {}
