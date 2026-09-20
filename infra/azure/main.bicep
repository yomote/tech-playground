targetScope = 'resourceGroup'

@description('Lowercase letters, digits, and hyphens; start with a letter and end with a letter or digit.')
@minLength(2)
@maxLength(20)
param namePrefix string = 'tech-playground'

@description('Azure region for the Container Apps environment and Portal.')
param location string = resourceGroup().location

@description('Prebuilt, anonymously pullable Portal image. Prefer an immutable tag or sha256 digest.')
@minLength(1)
param containerImage string

@description('Single-tenant Entra tenant ID. Anonymous access is never enabled by this template.')
@minLength(36)
@maxLength(36)
param tenantId string

@description('Existing single-tenant Entra application client ID used for Portal sign-in.')
@minLength(36)
@maxLength(36)
param clientId string

@description('Explicit Entra user object IDs allowed to access the Portal; tenant membership alone does not grant access.')
@minLength(1)
param allowedUserObjectIds string[]

@description('Existing user-assigned managed identity resource ID. It must already have Key Vault Secrets User access to the referenced secret.')
@minLength(1)
param authManagedIdentityResourceId string

@description('Existing Key Vault secret HTTPS URI containing the Entra application client secret. Never pass a plaintext client secret.')
@minLength(1)
@secure()
param authClientSecretKeyVaultUri string

@description('Keep false for initial deployment. Publish only after the enabled auth configuration and explicit user allowlist have been verified on the existing app.')
param publishIngress bool = false

param tags object = {
  project: 'tech-playground'
  component: 'portal'
}

var authSecretName = 'microsoft-provider-authentication-secret'

resource environment 'Microsoft.App/managedEnvironments@2026-01-01' = {
  name: '${namePrefix}-env'
  location: location
  tags: tags
  properties: {
    publicNetworkAccess: 'Enabled'
    appLogsConfiguration: {
      destination: 'none'
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource portal 'Microsoft.App/containerApps@2026-01-01' = {
  name: '${namePrefix}-portal'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${authManagedIdentityResourceId}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: publishIngress
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
      }
      secrets: [
        {
          name: authSecretName
          keyVaultUrl: authClientSecretKeyVaultUri
          identity: authManagedIdentityResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'portal'
          image: containerImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          probes: [
            {
              type: 'Readiness'
              httpGet: {
                path: '/healthz'
                port: 8080
              }
              initialDelaySeconds: 2
              periodSeconds: 10
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8080
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
}

resource portalAuth 'Microsoft.App/containerApps/authConfigs@2026-01-01' = {
  parent: portal
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
      // Probes connect directly to the container; all external routes require sign-in.
      excludedPaths: []
    }
    httpSettings: {
      requireHttps: true
      routes: {
        apiPrefix: '/.auth'
      }
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: clientId
          clientSecretSettingName: authSecretName
          openIdIssuer: '${az.environment().authentication.loginEndpoint}${tenantId}/v2.0'
        }
        validation: {
          defaultAuthorizationPolicy: {
            allowedPrincipals: {
              identities: allowedUserObjectIds
            }
          }
        }
      }
    }
    login: {
      tokenStore: {
        enabled: false
      }
    }
  }
}

// Internal ingress adds `.internal.` to the current FQDN. Register the future
// public callback before publishing, so the redirect URI does not change at launch.
output portalUrl string = 'https://${portal.name}.${environment.properties.defaultDomain}'
output currentIngressUrl string = 'https://${portal.properties.configuration.ingress.fqdn}'
output containerAppName string = portal.name
output environmentName string = environment.name
output authConfigId string = portalAuth.id
output externalIngressPublished bool = publishIngress
