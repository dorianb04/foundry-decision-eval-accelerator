param location string
param storageName string
param vnetName string
param nsgName string
param privateEndpointName string
param tags object

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}
resource nsg 'Microsoft.Network/networkSecurityGroups@2024-07-01' = {
  name: nsgName
  location: location
  tags: tags
  properties: { securityRules: [] }
}
resource vnet 'Microsoft.Network/virtualNetworks@2024-07-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.210.42.0/24'] }
    subnets: [
      {
        name: 'functions'
        properties: {
          addressPrefix: '10.210.42.0/26'
          networkSecurityGroup: { id: nsg.id }
          delegations: [
            { name: 'flex', properties: { serviceName: 'Microsoft.App/environments' } }
          ]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: '10.210.42.64/28'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}
resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.blob.core.windows.net'
  location: 'global'
  tags: tags
}
resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: 'function-vnet'
  location: 'global'
  properties: {
    virtualNetwork: { id: vnet.id }
    registrationEnabled: false
  }
}
resource endpoint 'Microsoft.Network/privateEndpoints@2024-07-01' = {
  name: privateEndpointName
  location: location
  tags: tags
  properties: {
    subnet: {
      id: resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'private-endpoints')
    }
    privateLinkServiceConnections: [
      {
        name: 'blob'
        properties: {
          privateLinkServiceId: storage.id
          groupIds: ['blob']
        }
      }
    ]
  }
}
resource dns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-07-01' = {
  parent: endpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'blob', properties: { privateDnsZoneId: zone.id } }
    ]
  }
}

output integrationSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'functions')
output privateEndpointId string = endpoint.id
