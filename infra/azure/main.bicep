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

param tags object = {
  project: 'tech-playground'
  component: 'portal'
}

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
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
      }
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

output portalUrl string = 'https://${portal.properties.configuration.ingress.fqdn}'
output containerAppName string = portal.name
output environmentName string = environment.name
