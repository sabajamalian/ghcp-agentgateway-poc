@description('Pilot Azure region.')
param location string = resourceGroup().location

@description('Operator public IPv4 CIDR, restricted to a single address.')
param operatorCidr string

@description('Dedicated SSH public key. Never provide a private key.')
param sshPublicKey string

param vmSize string = 'Standard_D2as_v7'
param name string = 'agw-inspection'
param adminUser string = 'pilot'
param sshPort int = 443

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${name}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 }
  }
}

resource table 'Microsoft.OperationalInsights/workspaces/tables@2022-10-01' = {
  parent: workspace
  name: 'CopilotModel_CL'
  properties: {
    plan: 'Analytics'
    retentionInDays: 30
    totalRetentionInDays: 30
    schema: {
      name: 'CopilotModel_CL'
      columns: [
        { name: 'TimeGenerated', type: 'dateTime' }
        { name: 'Metadata', type: 'dynamic' }
      ]
    }
  }
}

resource endpoint 'Microsoft.Insights/dataCollectionEndpoints@2023-03-11' = {
  name: '${name}-dce'
  location: location
  kind: 'Linux'
  properties: {
    networkAcls: { publicNetworkAccess: 'Enabled' }
  }
}

resource rule 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: '${name}-dcr'
  location: location
  kind: 'Linux'
  properties: {
    dataCollectionEndpointId: endpoint.id
    streamDeclarations: {
      'Custom-CopilotModelRaw': {
        columns: [
          { name: 'TimeGenerated', type: 'datetime' }
          { name: 'Payload', type: 'string' }
          { name: 'FilePath', type: 'string' }
          { name: 'Computer', type: 'string' }
        ]
      }
    }
    dataSources: {
      logFiles: [
        {
          name: 'model-metadata'
          streams: ['Custom-CopilotModelRaw']
          filePatterns: ['/var/log/inspection-poc/metadata-*.jsonl']
          format: 'json'
        }
      ]
    }
    destinations: {
      logAnalytics: [
        { name: 'pilot-workspace', workspaceResourceId: workspace.id }
      ]
    }
    dataFlows: [
      {
        streams: ['Custom-CopilotModelRaw']
        destinations: ['pilot-workspace']
        transformKql: 'source | extend Metadata = parse_json(Payload) | where Metadata.poc_schema == "copilot-model-v1" | project TimeGenerated = todatetime(Metadata.timestamp), Metadata'
        outputStream: 'Custom-CopilotModel_CL'
      }
    ]
  }
  dependsOn: [table]
}

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${name}-nsg'
  location: location
  properties: {
    securityRules: [
      {
        name: 'ssh-operator-only'
        properties: {
          priority: 100
          access: 'Allow'
          direction: 'Inbound'
          protocol: 'Tcp'
          sourceAddressPrefix: operatorCidr
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: string(sshPort)
        }
      }
      {
        name: 'deny-other-inbound'
        properties: {
          priority: 200
          access: 'Deny'
          direction: 'Inbound'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${name}-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.91.0.0/24'] }
    subnets: [
      {
        name: 'pilot'
        properties: {
          addressPrefix: '10.91.0.0/27'
          networkSecurityGroup: { id: nsg.id }
        }
      }
    ]
  }
}

resource ip 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${name}-ssh-ip'
  location: location
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: '${name}-nic'
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'primary'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          subnet: { id: '${vnet.id}/subnets/pilot' }
          publicIPAddress: { id: ip.id }
        }
      }
    ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: '${name}-vm'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: vmSize }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        deleteOption: 'Delete'
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
      }
    }
    osProfile: {
      computerName: name
      adminUsername: adminUser
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            { path: '/home/${adminUser}/.ssh/authorized_keys', keyData: sshPublicKey }
          ]
        }
      }
    }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
    }
    networkProfile: {
      networkInterfaces: [{ id: nic.id }]
    }
    diagnosticsProfile: { bootDiagnostics: { enabled: true } }
  }
}

resource monitor 'Microsoft.Compute/virtualMachines/extensions@2024-07-01' = {
  parent: vm
  name: 'AzureMonitorLinuxAgent'
  location: location
  properties: {
    publisher: 'Microsoft.Azure.Monitor'
    type: 'AzureMonitorLinuxAgent'
    typeHandlerVersion: '1.0'
    autoUpgradeMinorVersion: true
    enableAutomaticUpgrade: true
    settings: {}
  }
}

resource association 'Microsoft.Insights/dataCollectionRuleAssociations@2023-03-11' = {
  name: 'pilot-metadata'
  scope: vm
  properties: { dataCollectionRuleId: rule.id }
}

resource endpointAssociation 'Microsoft.Insights/dataCollectionRuleAssociations@2023-03-11' = {
  name: 'configurationAccessEndpoint'
  scope: vm
  properties: { dataCollectionEndpointId: endpoint.id }
}

output sshIp string = ip.properties.ipAddress
output vmName string = vm.name
output workspaceId string = workspace.properties.customerId
output workspaceResourceId string = workspace.id
