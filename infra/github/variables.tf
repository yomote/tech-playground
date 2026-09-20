variable "owner" {
  type        = string
  description = "Owner of the existing GitHub repository."
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9-]*$", var.owner))
    error_message = "Specify a GitHub user or organization name."
  }
}

variable "repository_name" {
  type        = string
  description = "Existing repository to import; do not include the owner."
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+$", var.repository_name))
    error_message = "Specify a repository name without an owner or slash."
  }
}

variable "visibility" {
  type        = string
  description = "Explicitly confirmed current visibility; never change it to unlock a feature."
  validation {
    condition     = contains(["private", "public"], var.visibility)
    error_message = "Explicitly set private or public."
  }
}

variable "enable_main_ruleset" {
  type        = bool
  default     = false
  description = "Enable only after confirming repository ruleset support and a successful readiness check."
}

variable "existing_ruleset_id" {
  type        = number
  default     = null
  nullable    = true
  description = "Existing tech-playground-main ruleset ID to import, if any."
  validation {
    condition     = var.existing_ruleset_id == null || var.enable_main_ruleset
    error_message = "An existing ruleset ID requires enable_main_ruleset=true."
  }
}

variable "enable_azure_environment" {
  type        = bool
  default     = false
  description = "Enable azure-production only after confirming repository environment and branch-policy support."
}

variable "import_azure_environment" {
  type        = bool
  default     = false
  description = "Import an existing azure-production environment rather than creating it."
  validation {
    condition     = !var.import_azure_environment || var.enable_azure_environment
    error_message = "Importing azure-production requires enable_azure_environment=true."
  }
}
