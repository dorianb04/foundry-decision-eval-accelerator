targetScope = 'subscription'

param resourceGroupName string
param location string
param environmentName string
param functionName string
param planName string
param storageName string
param foundryAccountName string
param foundryEndpoint string
param deployerObjectId string
param sessionId string
param deployedBy string
param createdAt string
@secure()
param evaluatorApiKey string
@secure()
param evaluatorConfigJson string = ''

module evaluator './modules/function.bicep' = {
  name: '${functionName}-resources'
  scope: resourceGroup(resourceGroupName)
  params: {
    location: location
    functionName: functionName
    planName: planName
    storageName: storageName
    foundryAccountName: foundryAccountName
    foundryEndpoint: foundryEndpoint
    deployerObjectId: deployerObjectId
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

output endpoint string = evaluator.outputs.endpoint
output functionResourceId string = evaluator.outputs.functionResourceId
output storageResourceId string = evaluator.outputs.storageResourceId
