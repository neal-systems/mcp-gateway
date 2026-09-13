# GitHub Actions OIDC provider.
#
# An AWS account may hold exactly one provider for a given URL, so this is
# conditional: create it here, or look up the one the account already has.
# No thumbprint is pinned -- IAM verifies token.actions.githubusercontent.com
# against its own trusted CA set, and a pinned thumbprint only adds a rotation
# outage waiting to happen.

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = []

  tags = {
    Name = "github-actions-oidc"
  }
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1

  url = "https://token.actions.githubusercontent.com"
}

locals {
  oidc_provider_arn = var.create_oidc_provider ? (
    aws_iam_openid_connect_provider.github[0].arn
    ) : (
    data.aws_iam_openid_connect_provider.github[0].arn
  )
}
