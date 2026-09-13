terraform {
  # 1.10 is the floor because the demo environment relies on S3 native state
  # locking (use_lockfile), which landed in 1.10 and removes the DynamoDB table.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Bootstrap keeps local state on purpose: it is what creates the bucket the
  # demo environment stores its state in, so it cannot store state there itself.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "mcp-gateway-demo"
      Owner       = "neal-systems"
      ManagedBy   = "terraform"
      Environment = "demo"
    }
  }
}
