targetScope = 'resourceGroup'

@description('Project name using lowercase letters, digits, and hyphens.')
@minLength(2)
@maxLength(18)
param namePrefix string = 'tech-playground'

@description('Short Azure region identifier using lowercase letters or digits, for example jpe for Japan East.')
@minLength(2)
@maxLength(3)
param regionCode string = 'jpe'

param location string = resourceGroup().location

@description('Principal object ID of the dedicated Portal managed identity created in the app resource group.')
@minLength(36)
@maxLength(36)
param authIdentityPrincipalId string

@description('Resource ID of the same dedicated app identity; used for a deterministic role assignment name.')
param authIdentityResourceId string

var tags = {
  project: namePrefix
  component: 'management'
  layer: 'management'
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2025-07-01' = {
  name: 'law-${namePrefix}-${regionCode}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  // Three region characters + a 13-character resource-group hash keeps this below 24 characters.
  name: 'kv-tp-${regionCode}-${uniqueString(resourceGroup().id)}'
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
    // Container Apps reaches this endpoint using its managed identity and the scoped role below.
    publicNetworkAccess: 'Enabled'
  }
}

resource secretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, authIdentityResourceId, 'Key Vault Secrets User')
  scope: keyVault
  properties: {
    principalId: authIdentityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}

output vaultName string = keyVault.name
output vaultURI string = keyVault.properties.vaultUri
output logAnalyticsWorkspaceName string = workspace.name
output logAnalyticsWorkspaceResourceId string = workspace.id
