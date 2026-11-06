targetScope = 'resourceGroup'

@description('Region of the existing Function and storage account.')
param location string
@description('Existing deployment storage account; never recreated here.')
param storageName string
@description('Dedicated evaluator virtual network.')
param vnetName string
@description('NSG for the Function integration subnet.')
param nsgName string
@description('Blob private endpoint name.')
param privateEndpointName string
@description('Cost and ownership tags.')
param tags object = {}

module network './modules/private-storage-network.bicep' = {
  name: '${vnetName}-resources'
  params: {
    location: location
    storageName: storageName
    vnetName: vnetName
    nsgName: nsgName
    privateEndpointName: privateEndpointName
    tags: tags
  }
}

output integrationSubnetId string = network.outputs.integrationSubnetId
output privateEndpointId string = network.outputs.privateEndpointId
