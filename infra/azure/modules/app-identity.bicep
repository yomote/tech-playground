targetScope = 'resourceGroup'

@description('Project name using lowercase letters, digits, and hyphens.')
@minLength(2)
@maxLength(18)
param namePrefix string = 'tech-playground'

@description('Short Azure region identifier, for example jpe for Japan East.')
@minLength(2)
@maxLength(3)
param regionCode string = 'jpe'

param location string = resourceGroup().location

resource portalIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${namePrefix}-portal-${regionCode}'
  location: location
  tags: {
    project: namePrefix
    component: 'portal-auth'
    layer: 'app'
  }
}

output identityResourceId string = portalIdentity.id
output principalId string = portalIdentity.properties.principalId
