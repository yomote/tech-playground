"""Compile the Portal template and check its default external-access boundary."""

import json
from pathlib import Path
import subprocess
import unittest


class PortalAuthenticationTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            ["bicep", "build", str(Path(__file__).with_name("main.bicep")), "--stdout"],
            check=True,
            capture_output=True,
            text=True,
        )
        cls.template = json.loads(result.stdout)
        cls.parameters = cls.template["parameters"]
        resources = cls.template["resources"]
        if isinstance(resources, dict):
            resources = resources.values()
        cls.portal = next(r for r in resources if r["type"] == "Microsoft.App/containerApps")
        cls.auth = next(r for r in resources if r["type"] == "Microsoft.App/containerApps/authConfigs")

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


if __name__ == "__main__":
    unittest.main()
