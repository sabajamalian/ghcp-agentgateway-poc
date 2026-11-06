// agentgateway as an authenticated CONNECT egress proxy for GitHub Copilot,
// running on ACI with a separate HTTPS UI and writable runtime configuration.

@description('Azure region.')
param location string = resourceGroup().location

@description('Base name for resources.')
param name string = 'agw-copilot'

@description('Globally unique DNS label for the proxy public IP.')
param dnsLabel string

@description('agentgateway container image.')
param image string = 'cr.agentgateway.dev/agentgateway:v1.7.0'

@description('Azure-hosted base image for the checksum-verified Caddy binary.')
param tlsBaseImage string = 'mcr.microsoft.com/azurelinux/base/core:3.0'

@description('Azure-hosted initialization image with curl and Python.')
param initImage string = 'mcr.microsoft.com/azure-cli:2.81.0'

@description('Port the proxy listens on.')
param proxyPort int = 3128

@description('Seed config.yaml contents. deploy.sh exports current UI changes before updating.')
@secure()
param configYaml string

@description('htpasswd file contents (bcrypt or apr1 hashes only).')
@secure()
param htpasswd string

@description('Caddy configuration for HTTPS and UI authentication.')
param caddyConfig string = loadTextContent('./Caddyfile')

@description('UI administrator username, separate from the Copilot proxy account.')
param uiUser string = 'admin'

@description('bcrypt hash of the UI administrator password.')
@secure()
param uiPasswordHash string

@description('Private CA-signed certificate for the UI origin.')
param uiOriginCert string

@description('Private key for the UI origin certificate.')
@secure()
param uiOriginKey string

@description('Public CA certificate trusted by the UI frontend.')
param uiOriginCaCert string

@description('Replace runtime configuration with the supplied seed instead of preserving the existing file.')
param replaceConfig bool = false

var proxyFqdn = '${dnsLabel}.${location}.azurecontainer.io'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${name}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource proxy 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: '${name}-aci'
  location: location
  properties: {
    osType: 'Linux'
    restartPolicy: 'Always'
    sku: 'Standard'
    initContainers: [
      {
        name: 'initialize-config'
        properties: {
          image: initImage
          command: [ '/bin/sh', '/bootstrap/initialize.sh' ]
          environmentVariables: [
            { name: 'REPLACE_CONFIG', value: string(replaceConfig) }
          ]
          volumeMounts: [
            { name: 'config', mountPath: '/config' }
            { name: 'bootstrap', mountPath: '/bootstrap', readOnly: true }
          ]
        }
      }
    ]
    containers: [
      {
        name: 'agentgateway'
        properties: {
          image: image
          command: [ '/app/agentgateway', '-f', '/config/config.yaml' ]
          ports: [ { port: proxyPort, protocol: 'TCP' } ]
          resources: { requests: { cpu: 1, memoryInGB: 1 } }
          volumeMounts: [
            { name: 'config', mountPath: '/config' }
            { name: 'bootstrap', mountPath: '/secrets', readOnly: true }
          ]
        }
      }
      {
        name: 'ui-https'
        properties: {
          image: tlsBaseImage
          command: [ '/data/bin/caddy', 'run', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile' ]
          ports: [
            { port: 443, protocol: 'TCP' }
          ]
          environmentVariables: [
            { name: 'UI_HOST', value: proxyFqdn }
            { name: 'UI_USER', value: uiUser }
            { name: 'UI_PASSWORD_HASH', secureValue: uiPasswordHash }
            { name: 'XDG_DATA_HOME', value: '/data' }
            { name: 'XDG_CONFIG_HOME', value: '/data/caddy-config' }
          ]
          resources: { requests: { cpu: json('0.5'), memoryInGB: json('0.5') } }
          volumeMounts: [
            { name: 'config', mountPath: '/data' }
            { name: 'bootstrap', mountPath: '/etc/caddy', readOnly: true }
          ]
        }
      }
    ]
    volumes: [
      {
        name: 'config'
        emptyDir: {}
      }
      {
        name: 'bootstrap'
        secret: {
          'config.yaml': base64(configYaml)
          htpasswd: base64(htpasswd)
          Caddyfile: base64(caddyConfig)
          'initialize.sh': base64(loadTextContent('./initialize.sh'))
          'install-caddy.sh': base64(loadTextContent('./install-caddy.sh'))
          'ui-origin.crt': base64(uiOriginCert)
          'ui-origin.key': base64(uiOriginKey)
        }
      }
    ]
    ipAddress: {
      type: 'Public'
      dnsNameLabel: dnsLabel
      ports: [
        { port: proxyPort, protocol: 'TCP' }
        { port: 443, protocol: 'TCP' }
      ]
    }
    diagnostics: {
      logAnalytics: {
        workspaceId: logs.properties.customerId
        workspaceKey: logs.listKeys().primarySharedKey
        logType: 'ContainerInstanceLogs'
      }
    }
  }
}

resource uiEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${name}-ui-env'
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

resource uiFrontend 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${name}-ui'
  location: location
  properties: {
    managedEnvironmentId: uiEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
      }
      secrets: [
        { name: 'caddy-config', value: loadTextContent('./ui-frontend.Caddyfile') }
        { name: 'origin-ca', value: uiOriginCaCert }
        { name: 'install-caddy', value: loadTextContent('./install-caddy.sh') }
      ]
    }
    template: {
      initContainers: [
        {
          name: 'install-caddy'
          image: initImage
          command: [ '/bin/sh', '/bootstrap/install-caddy.sh' ]
          env: [ { name: 'CADDY_TOOLS_DIR', value: '/tools' } ]
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          volumeMounts: [
            { volumeName: 'tools', mountPath: '/tools' }
            { volumeName: 'settings', mountPath: '/bootstrap' }
          ]
        }
      ]
      containers: [
        {
          name: 'ui'
          image: tlsBaseImage
          command: [ '/tools/caddy', 'run', '--config', '/etc/ui/Caddyfile', '--adapter', 'caddyfile' ]
          probes: [
            {
              type: 'Readiness'
              tcpSocket: { port: 8080 }
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
          env: [
            { name: 'ORIGIN_HOST', value: proxyFqdn }
            { name: 'SSL_CERT_FILE', value: '/etc/ui/origin-ca.crt' }
          ]
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          volumeMounts: [
            { volumeName: 'tools', mountPath: '/tools' }
            { volumeName: 'settings', mountPath: '/etc/ui' }
          ]
        }
      ]
      volumes: [
        { name: 'tools', storageType: 'EmptyDir' }
        {
          name: 'settings'
          storageType: 'Secret'
          secrets: [
            { secretRef: 'caddy-config', path: 'Caddyfile' }
            { secretRef: 'origin-ca', path: 'origin-ca.crt' }
            { secretRef: 'install-caddy', path: 'install-caddy.sh' }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

output proxyFqdn string = proxy.properties.ipAddress.fqdn
output proxyIp string = proxy.properties.ipAddress.ip
output proxyPort int = proxyPort
output logAnalyticsWorkspace string = logs.name
output uiUrl string = 'https://${uiFrontend.properties.configuration.ingress.fqdn}/ui/'
