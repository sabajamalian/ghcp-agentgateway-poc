@description('Resource region.')
param location string = resourceGroup().location

param name string = 'agw-byok'
param image string = 'cr.agentgateway.dev/agentgateway:v1.7.0'

@secure()
param configJson string

@secure()
param foundryApiKey string

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${name}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${name}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        { name: 'gateway-config', value: configJson }
        { name: 'foundry-key', value: foundryApiKey }
      ]
    }
    template: {
      containers: [
        {
          name: 'agentgateway'
          image: image
          command: ['/app/agentgateway', '-f', '/secrets/config.json']
          resources: { cpu: json('0.5'), memory: '1Gi' }
          volumeMounts: [
            { volumeName: 'settings', mountPath: '/secrets' }
          ]
          probes: [
            {
              type: 'Readiness'
              tcpSocket: { port: 8080 }
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'settings'
          storageType: 'Secret'
          secrets: [
            { secretRef: 'gateway-config', path: 'config.json' }
            { secretRef: 'foundry-key', path: 'foundry-api-key' }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

output baseUrl string = 'https://${app.properties.configuration.ingress.fqdn}/v1'
output logAnalyticsWorkspace string = logs.name
