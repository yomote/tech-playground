# Import blocks intentionally fail for an unknown repository instead of creating one.
import {
  to = github_repository.project
  id = var.repository_name
}
import {
  to = github_branch_default.main
  id = var.repository_name
}
import {
  to = github_actions_repository_permissions.project
  id = var.repository_name
}
import {
  to = github_workflow_repository_permissions.project
  id = var.repository_name
}
import {
  to = github_repository_vulnerability_alerts.project
  id = var.repository_name
}
import {
  to = github_repository_dependabot_security_updates.project
  id = var.repository_name
}
import {
  for_each = var.existing_ruleset_id == null ? {} : { main = var.existing_ruleset_id }
  to       = github_repository_ruleset.main[each.key]
  id       = "${var.repository_name}:${each.value}"
}
import {
  for_each = var.import_azure_environment ? { production = true } : {}
  to       = github_repository_environment.azure_production[each.key]
  id       = "${var.repository_name}:azure-production"
}
