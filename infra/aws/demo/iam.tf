data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

# The alias exists once the account has used SSM Parameter Store with a
# SecureString. Put the demo parameters (scripts/cloud/put_parameter.sh) before
# the first apply, which you have to do anyway for the instance to start.
data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

locals {
  ssm_prefix = "/mcp-gateway/demo/"

  ssm_parameter_arn_pattern = join("", [
    "arn:${data.aws_partition.current.partition}:ssm:${var.region}:",
    "${data.aws_caller_identity.current.account_id}:parameter",
    "${local.ssm_prefix}*",
  ])
}

data "aws_iam_policy_document" "instance_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "mcpgw-demo-instance"
  description        = "Instance role for the mcp-gateway demo host."
  assume_role_policy = data.aws_iam_policy_document.instance_assume.json

  tags = {
    Name = "mcpgw-demo-instance"
  }
}

# SSM Session Manager and RunShellScript. This is the whole reason the host
# needs no SSH key and no inbound port 22.
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "instance_parameters" {
  statement {
    sid    = "ReadDemoParameters"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
    ]
    resources = [local.ssm_parameter_arn_pattern]
  }

  # SecureString values are encrypted with the AWS-managed aws/ssm key. The
  # ViaService condition keeps this grant from being usable for anything but
  # Parameter Store decryption.
  statement {
    sid       = "DecryptParameterValues"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "instance_parameters" {
  name   = "mcpgw-demo-instance-parameters"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance_parameters.json
}

resource "aws_iam_instance_profile" "instance" {
  name = "mcpgw-demo-instance"
  role = aws_iam_role.instance.name

  tags = {
    Name = "mcpgw-demo-instance"
  }
}
