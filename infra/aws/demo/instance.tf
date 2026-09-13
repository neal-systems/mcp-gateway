# Always the current AL2023 x86_64 image. Resolving it through the public SSM
# parameter keeps the AMI id out of git and out of every plan diff review.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  user_data = templatefile("${path.module}/templates/user-data.yaml.tftpl", {
    bootstrap_sh    = base64gzip(file("${path.module}/../../../scripts/cloud/instance/bootstrap.sh"))
    gateway_release = base64gzip(file("${path.module}/../../../scripts/cloud/instance/gateway-release"))
    region          = var.region
    ssm_prefix      = local.ssm_prefix
  })
}

resource "aws_instance" "this" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.instance.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  availability_zone      = local.az

  # IMDSv2 only, and one hop: a container on this host cannot reach the
  # instance credentials through the metadata service.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 16
    encrypted             = true
    delete_on_termination = true

    tags = {
      Name = "${local.name}-root"
    }
  }

  user_data = local.user_data

  # Editing the instance scripts must not silently recycle a running demo.
  # Re-running bootstrap.sh over SSM is the supported way to pick up a change.
  user_data_replace_on_change = false

  # Detailed monitoring is a per-instance charge for one host nobody is paging
  # on. Basic five-minute CloudWatch metrics are enough.
  monitoring = false

  tags = {
    Name       = local.name
    SourceRepo = var.github_repo
  }
}

resource "aws_eip" "this" {
  instance = aws_instance.this.id
  domain   = "vpc"

  tags = {
    Name = local.name
  }

  depends_on = [aws_internet_gateway.this]
}
