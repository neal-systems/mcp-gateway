variable "region" {
  description = "AWS region for the state bucket and the deploy role."
  type        = string
  default     = "us-east-2"
}

variable "create_oidc_provider" {
  description = <<-EOT
    Create the GitHub Actions OIDC provider in this account. Set to false when
    the account already has token.actions.githubusercontent.com registered, in
    which case the existing provider is looked up and reused. Reusing a shared
    provider means other repositories in the account can also present tokens to
    it, so the trust policy on the role is the only thing keeping them out --
    confirm you are authorised to attach a new role to it before doing so.
  EOT
  type        = bool
  default     = true
}

variable "github_repo" {
  description = "owner/repo allowed to assume the deploy role."
  type        = string
  default     = "neal-systems/mcp-gateway"
}

variable "github_environment" {
  description = "GitHub Actions environment whose jobs may assume the role."
  type        = string
  default     = "demo"
}

variable "state_noncurrent_expiration_days" {
  description = "Days after which noncurrent state object versions expire."
  type        = number
  default     = 30
}
