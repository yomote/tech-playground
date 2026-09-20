targetScope = 'subscription'

@minLength(2)
@maxLength(18)
param namePrefix string = 'tech-playground'

@minLength(2)
@maxLength(3)
param regionCode string = 'jpe'

param location string = 'japaneast'

@description('Prebuilt Portal image; use a digest or immutable tag.')
@minLength(1)
param containerImage string

@description('Existing Entra tenant; Azure runtime resources are dedicated to Tech Playground.')
@minLength(36)
@maxLength(36)
param tenantId string

@description('Dedicated Tech Playground single-tenant Entra application client ID.')
@minLength(36)
@maxLength(36)
param clientId string

@minLength(1)
param allowedUserObjectIds string[]

@description('Name of the already populated client-secret entry in the dedicated management Key Vault; never a secret value.')
@minLength(1)
@maxLength(127)
param authClientSecretName string = 'portal-entra-client-secret'

@description('Initial deployment must remain internal; verify Entra and future public callback before enabling ingress.')
param publishIngress bool = false

// Run auth-foundation.bicep first to create the two groups and populate Key Vault.
// Reapplying the same foundation here keeps names, scope, identity and logging consistent.
module foundation './auth-foundation.bicep' = {
  name: '${namePrefix}-foundation'
  params: {
    namePrefix: namePrefix
    regionCode: regionCode
    location: location
  }
}

module portal './modules/portal.bicep' = {
  name: '${namePrefix}-portal'
  // Module scope must be known before deployment; output references below
  // still ensure the foundation finishes before the Portal starts.
  scope: resourceGroup('rg-${namePrefix}-app-${regionCode}')
  params: {
    namePrefix: namePrefix
    regionCode: regionCode
    location: location
    containerImage: containerImage
    tenantId: tenantId
    clientId: clientId
    allowedUserObjectIds: allowedUserObjectIds
    authManagedIdentityResourceId: foundation.outputs.identityResourceId
    authClientSecretKeyVaultUri: '${foundation.outputs.vaultURI}secrets/${authClientSecretName}'
    managementResourceGroupName: foundation.outputs.managementResourceGroupName
    logAnalyticsWorkspaceName: foundation.outputs.logAnalyticsWorkspaceName
    publishIngress: publishIngress
  }
}

output appResourceGroupName string = foundation.outputs.appResourceGroupName
output managementResourceGroupName string = foundation.outputs.managementResourceGroupName
output portalUrl string = portal.outputs.portalUrl
output currentIngressUrl string = portal.outputs.currentIngressUrl
output containerAppName string = portal.outputs.containerAppName
output environmentName string = portal.outputs.environmentName
output authConfigId string = portal.outputs.authConfigId
output externalIngressPublished bool = portal.outputs.externalIngressPublished
