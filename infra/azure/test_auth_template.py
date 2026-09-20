"""Compile Azure IaC and verify auth boundaries and dedicated resource ownership."""

from functools import lru_cache
import json
from pathlib import Path
import re
import subprocess
import unittest


@lru_cache(maxsize=None)
def compile_template(relative_path):
    result = subprocess.run(
        ["bicep", "build", str(Path(__file__).parent / relative_path), "--stdout"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def resources_of(template, resource_type):
    resources = template.get("resources", [])
    if isinstance(resources, dict):
        resources = resources.values()
    return [resource for resource in resources if resource["type"] == resource_type]


def nested_templates(template):
    yield template
    for deployment in resources_of(template, "Microsoft.Resources/deployments"):
        yield from nested_templates(deployment["properties"]["template"])


def deployment_with_resource(template, resource_type):
    return next(
        deployment
        for deployment in resources_of(template, "Microsoft.Resources/deployments")
        if resources_of(deployment["properties"]["template"], resource_type)
    )


def resource_reference_token(template, resource):
    resources = template["resources"]
    if isinstance(resources, dict):
        return next(name for name, candidate in resources.items() if candidate == resource)
    return resource["name"][1:-1]


def resolve_variable(template, expression):
    """Resolve whole-variable references without pretending to evaluate ARM."""
    seen = set()
    while isinstance(expression, str):
        match = re.fullmatch(r"\[variables\('([^']+)'\)\]", expression)
        if not match:
            return expression
        name = match.group(1)
        if name in seen:
            raise AssertionError("Cyclic name variable")
        seen.add(name)
        expression = template["variables"][name]
    return expression


class PortalAuthenticationTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = compile_template("modules/portal.bicep")
        cls.parameters = cls.template["parameters"]
        cls.portal = resources_of(cls.template, "Microsoft.App/containerApps")[0]
        cls.auth = resources_of(cls.template, "Microsoft.App/containerApps/authConfigs")[0]

    def test_initial_deployment_is_internal_and_auth_inputs_are_required(self):
        self.assertIs(self.parameters["publishIngress"]["defaultValue"], False)
        self.assertEqual(self.portal["properties"]["configuration"]["ingress"]["external"], "[parameters('publishIngress')]")
        for name in ("tenantId", "clientId", "allowedUserObjectIds", "authManagedIdentityResourceId", "authClientSecretKeyVaultUri"):
            self.assertNotIn("defaultValue", self.parameters[name])
        self.assertEqual(self.parameters["allowedUserObjectIds"]["minLength"], 1)

    def test_auth_has_no_anonymous_route_and_enforces_user_allowlist(self):
        auth = self.auth["properties"]
        self.assertIs(auth["platform"]["enabled"], True)
        self.assertEqual(auth["globalValidation"]["unauthenticatedClientAction"], "RedirectToLoginPage")
        self.assertEqual(auth["globalValidation"]["redirectToProvider"], "azureactivedirectory")
        self.assertEqual(auth["globalValidation"]["excludedPaths"], [])
        self.assertIs(auth["httpSettings"]["requireHttps"], True)
        provider = auth["identityProviders"]["azureActiveDirectory"]
        self.assertIs(provider["enabled"], True)
        self.assertEqual(provider["validation"]["defaultAuthorizationPolicy"]["allowedPrincipals"]["identities"], "[parameters('allowedUserObjectIds')]")
        self.assertIn("parameters('tenantId')", provider["registration"]["openIdIssuer"])

    def test_client_secret_is_a_key_vault_reference_on_the_app_identity(self):
        self.assertEqual(self.portal["identity"]["type"], "UserAssigned")
        secrets = self.portal["properties"]["configuration"]["secrets"]
        self.assertEqual(len(secrets), 1)
        self.assertNotIn("value", secrets[0])
        self.assertEqual(secrets[0]["keyVaultUrl"], "[parameters('authClientSecretKeyVaultUri')]")
        self.assertEqual(secrets[0]["identity"], "[parameters('authManagedIdentityResourceId')]")
        self.assertEqual(self.parameters["authClientSecretKeyVaultUri"]["type"].lower(), "securestring")
        self.assertFalse(self.portal["properties"]["configuration"]["ingress"]["allowInsecure"])

    def test_logs_reference_the_dedicated_management_workspace_without_key_inputs(self):
        workspace = resources_of(self.template, "Microsoft.OperationalInsights/workspaces")[0]
        self.assertIs(workspace["existing"], True)
        self.assertEqual(workspace["resourceGroup"], "[parameters('managementResourceGroupName')]")
        self.assertEqual(workspace["name"], "[parameters('logAnalyticsWorkspaceName')]")
        environment = resources_of(self.template, "Microsoft.App/managedEnvironments")[0]
        logging = environment["properties"]["appLogsConfiguration"]
        self.assertEqual(logging["destination"], "log-analytics")
        # Both expressions must resolve the same cross-resource-group workspace.
        logs_symbol = next(
            name for name, resource in self.template["resources"].items()
            if resource == workspace
        )
        configuration = logging["logAnalyticsConfiguration"]
        self.assertEqual(configuration["customerId"], f"[reference('{logs_symbol}').customerId]")
        self.assertIn(f"listKeys('{logs_symbol}',", configuration["sharedKey"])
        self.assertTrue(configuration["sharedKey"].endswith(".primarySharedKey]"))
        self.assertFalse(any("sharedkey" in name.lower() for name in self.parameters))

    def test_future_public_url_does_not_depend_on_internal_ingress_hostname(self):
        public_url = self.template["outputs"]["portalUrl"]["value"]
        self.assertIn(".defaultDomain", public_url)
        self.assertNotIn("ingress.fqdn", public_url)
        self.assertNotIn(".internal.", public_url)
        self.assertIn("ingress.fqdn", self.template["outputs"]["currentIngressUrl"]["value"])


class AzureResourceOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = compile_template("main.bicep")
        cls.foundation = compile_template("auth-foundation.bicep")
        cls.identity_deployment = deployment_with_resource(cls.foundation, "Microsoft.ManagedIdentity/userAssignedIdentities")
        cls.management_deployment = deployment_with_resource(cls.foundation, "Microsoft.KeyVault/vaults")
        cls.management = cls.management_deployment["properties"]["template"]

    def test_subscription_roots_create_separate_application_and_management_groups(self):
        for template in (self.main, self.foundation):
            self.assertIn("subscriptionDeploymentTemplate", template["$schema"])
            parameters = template["parameters"]
            self.assertEqual(parameters["namePrefix"]["defaultValue"], "tech-playground")
            self.assertEqual(parameters["namePrefix"]["maxLength"], 18)
            self.assertEqual(parameters["regionCode"]["defaultValue"], "jpe")
            self.assertEqual(parameters["regionCode"]["maxLength"], 3)
            self.assertEqual(parameters["location"]["defaultValue"], "japaneast")
        groups = resources_of(self.foundation, "Microsoft.Resources/resourceGroups")
        self.assertEqual(len(groups), 2)
        names = {resolve_variable(self.foundation, group["name"]) for group in groups}
        self.assertEqual(names, {
            "[format('rg-{0}-app-{1}', parameters('namePrefix'), parameters('regionCode'))]",
            "[format('rg-{0}-mgmt-{1}', parameters('namePrefix'), parameters('regionCode'))]",
        })
        self.assertEqual(self.identity_deployment["resourceGroup"], self.foundation["outputs"]["appResourceGroupName"]["value"])
        self.assertEqual(self.management_deployment["resourceGroup"], self.foundation["outputs"]["managementResourceGroupName"]["value"])
        self.assertNotEqual(self.identity_deployment["resourceGroup"], self.management_deployment["resourceGroup"])

    def test_app_identity_gets_only_the_management_vault_scoped_secret_reader_role(self):
        identity_template = self.identity_deployment["properties"]["template"]
        self.assertEqual(len(resources_of(identity_template, "Microsoft.ManagedIdentity/userAssignedIdentities")), 1)
        self.assertEqual(resources_of(identity_template, "Microsoft.KeyVault/vaults"), [])
        self.assertEqual(resources_of(identity_template, "Microsoft.OperationalInsights/workspaces"), [])
        self.assertEqual(len(resources_of(self.management, "Microsoft.KeyVault/vaults")), 1)
        self.assertEqual(len(resources_of(self.management, "Microsoft.OperationalInsights/workspaces")), 1)
        self.assertEqual(resources_of(self.management, "Microsoft.ManagedIdentity/userAssignedIdentities"), [])
        binding = self.management_deployment["properties"]["parameters"]["authIdentityPrincipalId"]["value"]
        self.assertIn(resource_reference_token(self.foundation, self.identity_deployment), binding)
        self.assertIn(".outputs.principalId.value", binding)
        vault = resources_of(self.management, "Microsoft.KeyVault/vaults")[0]
        self.assertIs(vault["properties"]["enableRbacAuthorization"], True)
        self.assertIs(vault["properties"]["enablePurgeProtection"], True)
        roles = resources_of(self.management, "Microsoft.Authorization/roleAssignments")
        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0]["scope"], f"[resourceId('Microsoft.KeyVault/vaults', {vault['name'][1:-1]})]")
        self.assertEqual(roles[0]["properties"]["principalId"], "[parameters('authIdentityPrincipalId')]")
        self.assertEqual(roles[0]["properties"]["principalType"], "ServicePrincipal")
        self.assertEqual(roles[0]["properties"]["roleDefinitionId"], "[subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')]")

    def test_main_uses_its_own_foundation_outputs_not_foreign_runtime_resource_inputs(self):
        self.assertEqual(set(self.main["parameters"]), {
            "namePrefix", "regionCode", "location", "containerImage", "tenantId", "clientId",
            "allowedUserObjectIds", "authClientSecretName", "publishIngress",
        })
        self.assertIs(self.main["parameters"]["publishIngress"]["defaultValue"], False)
        self.assertEqual(self.main["parameters"]["authClientSecretName"]["defaultValue"], "portal-entra-client-secret")
        foundation = deployment_with_resource(self.main, "Microsoft.Resources/resourceGroups")
        portal = deployment_with_resource(self.main, "Microsoft.App/containerApps")
        self.assertEqual(resolve_variable(self.main, portal["resourceGroup"]), self.foundation["outputs"]["appResourceGroupName"]["value"])
        foundation_name = resource_reference_token(self.main, foundation)
        self.assertTrue(any(foundation_name in dependency for dependency in portal["dependsOn"]))
        bindings = portal["properties"]["parameters"]
        for parameter, output in {
            "authManagedIdentityResourceId": "identityResourceId",
            "authClientSecretKeyVaultUri": "vaultURI",
            "managementResourceGroupName": "managementResourceGroupName",
            "logAnalyticsWorkspaceName": "logAnalyticsWorkspaceName",
        }.items():
            expression = bindings[parameter]["value"]
            self.assertIn(foundation_name, expression)
            self.assertIn(f".outputs.{output}.value", expression)
        secret_uri = bindings["authClientSecretKeyVaultUri"]["value"]
        self.assertIn("secrets/", secret_uri)
        self.assertIn("parameters('authClientSecretName')", secret_uri)
        self.assertEqual(bindings["publishIngress"]["value"], "[parameters('publishIngress')]")

    def test_no_nested_template_exports_log_analytics_shared_keys(self):
        for root in (self.main, self.foundation):
            for template in nested_templates(root):
                outputs = json.dumps(template.get("outputs", {})).lower()
                self.assertNotIn("sharedkey", outputs)
                self.assertNotIn("listkeys(", outputs)


if __name__ == "__main__":
    unittest.main()
