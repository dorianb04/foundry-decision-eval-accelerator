targetScope = 'subscription'

param resourceGroupName string
param location string
param environmentName string
param appName string
param identityName string
param existingEnvironmentName string
param existingRegistryName string
param foundryAccountName string
param foundryEndpoint string
param containerImage string
param sessionId string
param deployedBy string
param createdAt string
param deployApp bool = true
@minValue(0)
@maxValue(1)
param minReplicas int = 0
@secure()
param evaluatorApiKey string
@secure()
param evaluatorConfigJson string = ''

module adapter './modules/adapter.bicep' = {
  name: '${appName}-resources'
  scope: resourceGroup(resourceGroupName)
  params: {
    location: location
    appName: appName
    identityName: identityName
    existingEnvironmentName: existingEnvironmentName
    existingRegistryName: existingRegistryName
    foundryAccountName: foundryAccountName
    foundryEndpoint: foundryEndpoint
    containerImage: containerImage
    deployApp: deployApp
    minReplicas: minReplicas
    evaluatorApiKey: evaluatorApiKey
    evaluatorConfigJson: evaluatorConfigJson
    tags: {
      'app-onboard-skill': 'true'
      'app-onboard-session-id': sessionId
      'created-at': createdAt
      environment: environmentName
      'deployed-by': deployedBy
    }
  }
}

output endpoint string = adapter.outputs.endpoint
output appResourceId string = adapter.outputs.appResourceId
output identityResourceId string = adapter.outputs.identityResourceId
