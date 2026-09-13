terraform {
  # Partial configuration. Supply the rest with:
  #   terraform init -backend-config=backend.hcl
  # backend.hcl is gitignored; backend.hcl.example is the template.
  backend "s3" {}
}
