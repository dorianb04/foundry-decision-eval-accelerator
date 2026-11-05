param location string
param functionName string
param planName string
param storageName string
param foundryAccountName string
param foundryEndpoint string
param deployerObjectId string
param tags object
@secure()
param evaluatorApiKey string
@secure()
param evaluatorConfigJson string

resource foundry 'Microsoft.CognitiveServices/accounts@2026-11-01' existing = {
  name: foundryAccountName
}
resource storage 'Microsoft.Storage/storageAccounts@2026-09-01' = {
  name: storageName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    publicNetworkAccess: 'Enabled'
  }
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2026-09-01' = {
  parent: storage
  name: 'default'
}
resource packages 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'deploymentpackage'
  properties: { publicAccess: 'None' }
}
resource plan 'Microsoft.Web/serverfarms@2026-08-01' = {
  name: planName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: { name: 'FC1', tier: 'FlexConsumption' }
  properties: { reserved: true }
}
resource function 'Microsoft.Web/sites@2026-08-01' = {
  name: functionName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    functionAppConfig: {
      runtime: { name: 'python', version: '3.12' }
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${packages.name}'
          authentication: { type: 'SystemAssignedIdentity' }
        }
      }
      scaleAndConcurrency: {
        instanceMemoryMB: 512
        maximumInstanceCount: 1
        triggers: {
          http: { perInstanceConcurrency: 8 }
        }
      }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'AZURE_AI_RESOURCE_ENDPOINT', value: foundryEndpoint }
        { name: 'DECISION_DEPLOYMENT', value: 'Decision-1' }
        { name: 'FOUNDRY_PREVIEW_COMPAT', value: '1' }
        { name: 'EVALUATOR_API_KEY', value: evaluatorApiKey }
        { name: 'EVALUATORS_CONFIG_JSON', value: evaluatorConfigJson }
      ]
    }
  }
}
resource scm 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: function
  name: 'scm'
  properties: { allow: false }
}
resource ftp 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: function
  name: 'ftp'
  properties: { allow: false }
}
resource hostStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, function.id, 'host-storage')
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
    principalId: function.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
resource publisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, deployerObjectId, 'package-publisher')
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: deployerObjectId
    principalType: 'User'
  }
}
resource inference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, function.id, 'inference')
  scope: foundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
    principalId: function.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output endpoint string = 'https://${function.properties.defaultHostName}'
output functionResourceId string = function.id
output storageResourceId string = storage.id
