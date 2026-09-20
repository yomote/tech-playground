terraform {
  required_version = ">= 1.16.1, < 2.0.0"
  required_providers {
    github = {
      source  = "integrations/github"
      version = "= 6.13.0"
    }
  }
  backend "local" {}
}

provider "github" {
  owner = var.owner
  # Use existing gh authentication or the process GITHUB_TOKEN. Never store a token here.
}
