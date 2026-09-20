targetScope = 'subscription'

@description('Tech Playground resource prefix; use lowercase letters, digits, and hyphens.')
@minLength(2)
@maxLength(18)
param namePrefix string = 'tech-playground'

@description('Short region suffix used in names; jpe denotes Japan East.')
@minLength(2)
@maxLength(3)
param regionCode string = 'jpe'

param location string = 'japaneast'

resource appGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-${namePrefix}-app-${regionCode}'
  location: location
  tags: {
    project: namePrefix
    layer: 'app'
  }
}

resource managementGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-${namePrefix}-mgmt-${regionCode}'
  location: location
  tags: {
    project: namePrefix
    layer: 'management'
  }
}

module appIdentity './modules/app-identity.bicep' = {
  name: '${namePrefix}-app-identity'
  scope: appGroup
  params: {
    namePrefix: namePrefix
    regionCode: regionCode
    location: location
  }
}

module management './modules/management.bicep' = {
  name: '${namePrefix}-management'
  scope: managementGroup
  params: {
    namePrefix: namePrefix
    regionCode: regionCode
    location: location
    authIdentityPrincipalId: appIdentity.outputs.principalId
    authIdentityResourceId: appIdentity.outputs.identityResourceId
  }
}

output appResourceGroupName string = appGroup.name
output managementResourceGroupName string = managementGroup.name
output identityResourceId string = appIdentity.outputs.identityResourceId
output vaultName string = management.outputs.vaultName
output vaultURI string = management.outputs.vaultURI
output logAnalyticsWorkspaceName string = management.outputs.logAnalyticsWorkspaceName
output logAnalyticsWorkspaceResourceId string = management.outputs.logAnalyticsWorkspaceResourceId
