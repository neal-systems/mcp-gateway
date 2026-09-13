variable "region" {
  description = "AWS region. Must match the region recorded at bootstrap."
  type        = string
  default     = "us-east-2"
}

variable "instance_type" {
  description = "EC2 instance type for the single demo host."
  type        = string
  default     = "t3.small"
}

variable "state_volume_gb" {
  description = "Size of the dedicated encrypted state volume, in GiB."
  type        = number
  default     = 4
}

variable "allowed_ingress_cidrs" {
  description = <<-EOT
    CIDRs allowed to reach ports 80 and 443. The default is the whole internet
    because the demo is an OAuth flow: GitHub redirects a browser back to this
    host, and Let's Encrypt has to reach port 80 to answer the HTTP-01
    challenge, so neither works from a pinned office range. Narrow it only if
    you are prepared to lose certificate issuance and third-party callbacks.
    There is no SSH rule at all; the only way in is SSM Session Manager.
  EOT
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "demo_hostname" {
  description = <<-EOT
    Public hostname for the demo. Leave empty to derive a working name from the
    Elastic IP via sslip.io, which needs no DNS zone. Set it to a real name once
    one exists; the GitHub OAuth callback URL must match whichever is used.
  EOT
  type        = string
  default     = ""
}

variable "github_repo" {
  description = "Source repository, recorded as a tag on the instance."
  type        = string
  default     = "neal-systems/mcp-gateway"
}
