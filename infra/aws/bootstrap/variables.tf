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

variable "github_owner_id" {
  description = <<-EOT
    Numeric id of the GitHub owner (organisation or user). Repositories created
    after 2026-07-15 present an immutable OIDC subject that embeds the owner
    and repository ids: repo:<owner>@<owner_id>/<repo>@<repo_id>:environment:<env>.
    Read it with: gh api orgs/<owner> --jq .id (or gh api users/<owner>).
  EOT
  type        = number
  default     = 324300420
}

variable "github_repo_id" {
  description = "Numeric id of the repository: gh api repos/<owner>/<repo> --jq .id"
  type        = number
  default     = 1355437034
}

variable "github_subject_format" {
  description = <<-EOT
    "immutable" (default; repositories created after 2026-07-15 or opted in)
    builds repo:<owner>@<owner_id>/<repo>@<repo_id>:environment:<env>.
    "legacy" builds repo:<owner>/<repo>:environment:<env> for older
    repositories that have not opted in. The deploy workflow prints the
    subject the live token actually carries so this can be checked.
  EOT
  type        = string
  default     = "immutable"

  validation {
    condition     = contains(["immutable", "legacy"], var.github_subject_format)
    error_message = "github_subject_format must be \"immutable\" or \"legacy\"."
  }
}
