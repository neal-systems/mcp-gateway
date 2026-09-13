output "state_bucket" {
  description = "S3 bucket holding the demo environment's Terraform state."
  value       = aws_s3_bucket.tfstate.id
}

output "deploy_role_arn" {
  description = "Role ARN for the GitHub Actions demo environment to assume."
  value       = aws_iam_role.github_deploy.arn
}

output "region" {
  description = "Region the state bucket and deploy role live in."
  value       = var.region
}

output "oidc_provider_arn" {
  description = "GitHub Actions OIDC provider ARN, created here or reused."
  value       = local.oidc_provider_arn
}

output "github_subject" {
  description = "The exact OIDC subject the deploy role trusts; compare with the subject printed by the deploy workflow."
  value       = local.github_subject
}
