mock_provider "github" {}

override_resource {
  target = github_repository.project
  values = { name = "test-repository" }
}
override_resource {
  target = github_branch_default.main
}
override_resource {
  target = github_actions_repository_permissions.project
}
override_resource {
  target = github_workflow_repository_permissions.project
}
override_resource {
  target = github_repository_vulnerability_alerts.project
}
override_resource {
  target = github_repository_dependabot_security_updates.project
}

variables {
  owner           = "test-owner"
  repository_name = "test-repository"
  visibility      = "private"
}

run "private_repository_defaults" {
  command = plan
  assert {
    condition     = github_repository.project.visibility == "private" && !github_repository.project.allow_auto_merge && github_repository.project.allow_squash_merge && !github_repository.project.allow_merge_commit && !github_repository.project.allow_rebase_merge
    error_message = "Keep explicit private visibility and squash-only merges."
  }
  assert {
    condition     = github_workflow_repository_permissions.project.default_workflow_permissions == "read" && !github_workflow_repository_permissions.project.can_approve_pull_request_reviews
    error_message = "Default Actions permissions must be read-only without PR self-approval."
  }
  assert {
    condition     = length(github_repository_ruleset.main) == 0 && length(github_repository_environment.azure_production) == 0
    error_message = "Plan-dependent rulesets and environments must be opt-in."
  }
}

run "auto_merge_without_ruleset_is_rejected" {
  command = plan
  variables {
    enable_security_auto_merge = true
  }
  expect_failures = [github_repository.project]
}

run "optional_policy" {
  command = plan
  variables {
    enable_main_ruleset      = true
    enable_azure_environment = true
  }
  assert {
    condition     = length(setsubtract(toset([for check in github_repository_ruleset.main["main"].rules[0].required_status_checks[0].required_check : check.context]), toset(["readiness", "github-settings-check", "osv-pnpm", "blue-security-verify"]))) == 0 && length(github_repository_ruleset.main["main"].rules[0].required_status_checks[0].required_check) == 4 && alltrue([for check in github_repository_ruleset.main["main"].rules[0].required_status_checks[0].required_check : check.integration_id == 15368])
    error_message = "All four CI checks must come from GitHub Actions."
  }
  assert {
    condition     = github_repository_ruleset.main["main"].rules[0].pull_request[0].required_approving_review_count == 1 && github_repository_ruleset.main["main"].rules[0].pull_request[0].require_last_push_approval && github_repository_ruleset.main["main"].rules[0].pull_request[0].dismiss_stale_reviews_on_push && length(github_repository_ruleset.main["main"].bypass_actors) == 0
    error_message = "Require independent approval of the latest push and no bypass actors."
  }
  assert {
    condition     = github_repository_environment_deployment_policy.azure_main["production"].branch_pattern == "main" && !github_repository_environment.azure_production["production"].can_admins_bypass
    error_message = "The optional deployment environment must be restricted to main."
  }
}

run "auto_merge_with_protected_main" {
  command = plan
  variables {
    enable_main_ruleset        = true
    enable_security_auto_merge = true
  }
  assert {
    condition     = github_repository.project.allow_auto_merge && github_repository_ruleset.main["main"].enforcement == "active" && github_repository_ruleset.main["main"].rules[0].pull_request[0].required_approving_review_count == 1
    error_message = "Auto-merge must be enabled only with the active main ruleset requiring review."
  }
}
