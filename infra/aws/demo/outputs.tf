locals {
  # sslip.io resolves a-b-c-d.sslip.io to a.b.c.d, which gives the demo a real
  # hostname that Let's Encrypt will issue for, with no DNS zone to own.
  derived_hostname = "${replace(aws_eip.this.public_ip, ".", "-")}.sslip.io"
  demo_hostname    = var.demo_hostname != "" ? var.demo_hostname : local.derived_hostname
}

output "instance_id" {
  description = "EC2 instance id; the SSM SendCommand target."
  value       = aws_instance.this.id
}

output "public_ip" {
  description = "Elastic IP attached to the demo host."
  value       = aws_eip.this.public_ip
}

output "demo_hostname" {
  description = "Hostname the demo is served on. Must match the OAuth callback."
  value       = local.demo_hostname
}

output "state_volume_id" {
  description = "EBS volume holding OAuth registrations and the signing key."
  value       = aws_ebs_volume.state.id
}

output "instance_role_arn" {
  description = "IAM role the instance assumes to read its SSM parameters."
  value       = aws_iam_role.instance.arn
}
