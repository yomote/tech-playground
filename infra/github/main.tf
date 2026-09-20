resource "github_repository" "project" {
  name                        = var.repository_name
  description                 = "Personal demo-first technology experiments: MCP Apps, Magentic orchestration, and OpenFGA."
  visibility                  = var.visibility
  has_issues                  = true
  has_projects                = false
  has_wiki                    = false
  allow_squash_merge          = true
  allow_merge_commit          = false
  allow_rebase_merge          = false
  allow_auto_merge            = false
  allow_update_branch         = true
  delete_branch_on_merge      = true
  squash_merge_commit_title   = "PR_TITLE"
  squash_merge_commit_message = "PR_BODY"
  archive_on_destroy          = true

  lifecycle {
    prevent_destroy = true
  }
}

resource "github_branch_default" "main" {
  repository = github_repository.project.name
  branch     = "main"
  rename     = false
}

resource "github_actions_repository_permissions" "project" {
  repository      = github_repository.project.name
  enabled         = true
  allowed_actions = "selected"
  allowed_actions_config {
    github_owned_allowed = true
    verified_allowed     = false
    patterns_allowed = [
      "Azure/login@*",
      "hashicorp/setup-terraform@*",
      "pnpm/action-setup@*",
    ]
  }
}

resource "github_workflow_repository_permissions" "project" {
  repository                       = github_repository.project.name
  default_workflow_permissions     = "read"
  can_approve_pull_request_reviews = false
}

resource "github_repository_vulnerability_alerts" "project" {
  repository = github_repository.project.name
  enabled    = true
}

resource "github_repository_dependabot_security_updates" "project" {
  repository = github_repository.project.name
  enabled    = true
  depends_on = [github_repository_vulnerability_alerts.project]
}

resource "github_repository_ruleset" "main" {
  for_each    = var.enable_main_ruleset ? { main = true } : {}
  name        = "tech-playground-main"
  repository  = github_repository.project.name
  target      = "branch"
  enforcement = "active"

  conditions {
    ref_name {
      include = ["refs/heads/main"]
      exclude = []
    }
  }

  # Solo development: no impossible self-approval requirement; no administrator bypass.
  rules {
    deletion                = true
    non_fast_forward        = true
    required_linear_history = true
    pull_request {
      required_approving_review_count   = 0
      required_review_thread_resolution = true
      dismiss_stale_reviews_on_push     = true
      require_code_owner_review         = false
      require_last_push_approval        = false
    }
    required_status_checks {
      strict_required_status_checks_policy = true
      required_check {
        context        = "readiness"
        integration_id = 15368
      }
    }
  }
}

resource "github_repository_environment" "azure_production" {
  for_each          = var.enable_azure_environment ? { production = true } : {}
  repository        = github_repository.project.name
  environment       = "azure-production"
  can_admins_bypass = false
  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}

resource "github_repository_environment_deployment_policy" "azure_main" {
  for_each       = var.enable_azure_environment ? { production = true } : {}
  repository     = github_repository.project.name
  environment    = github_repository_environment.azure_production[each.key].environment
  branch_pattern = "main"
}
