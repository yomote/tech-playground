targetScope = 'resourceGroup'

@description('Lowercase letters, digits, and hyphens; start with a letter and end with a letter or digit.')
@minLength(2)
@maxLength(20)
param namePrefix string = 'tech-playground'

@description('Azure region for the dedicated Portal authentication identity and Key Vault.')
param location string = resourceGroup().location

var tags = {
  project: 'tech-playground'
  component: 'portal-auth'
}

resource authIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: '${namePrefix}-auth-identity'
  location: location
  tags: tags
}

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  // Seven prefix characters + four separators/suffix characters + 13-character hash = 24.
  name: '${take(replace(namePrefix, '-', ''), 7)}-kv-${uniqueString(resourceGroup().id, namePrefix)}'
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 7
    // Access to secret contents still requires an explicit data-plane RBAC grant.
    publicNetworkAccess: 'Enabled'
  }
}

resource secretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, authIdentity.id, 'Key Vault Secrets User')
  scope: keyVault
  properties: {
    principalId: authIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}

output identityResourceId string = authIdentity.id
output vaultName string = keyVault.name
output vaultURI string = keyVault.properties.vaultUri
