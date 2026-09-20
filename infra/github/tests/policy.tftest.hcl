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

run "optional_policy" {
  command = plan
  variables {
    enable_main_ruleset      = true
    enable_azure_environment = true
  }
  assert {
    condition     = one(github_repository_ruleset.main["main"].rules[0].required_status_checks[0].required_check).context == "readiness" && one(github_repository_ruleset.main["main"].rules[0].required_status_checks[0].required_check).integration_id == 15368
    error_message = "Only the readiness check issued by GitHub Actions can satisfy the ruleset."
  }
  assert {
    condition     = github_repository_ruleset.main["main"].rules[0].pull_request[0].required_approving_review_count == 0 && length(github_repository_ruleset.main["main"].bypass_actors) == 0
    error_message = "Solo development should need no self-approval and have no administrator bypass."
  }
  assert {
    condition     = github_repository_environment_deployment_policy.azure_main["production"].branch_pattern == "main" && !github_repository_environment.azure_production["production"].can_admins_bypass
    error_message = "The optional deployment environment must be restricted to main."
  }
}
