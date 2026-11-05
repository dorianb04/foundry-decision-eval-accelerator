param location string
param appName string
param identityName string
param existingEnvironmentName string
param existingRegistryName string
param foundryAccountName string
param foundryEndpoint string
param containerImage string
param deployApp bool
param minReplicas int
param tags object
@secure()
param evaluatorApiKey string
@secure()
param evaluatorConfigJson string

resource hosting 'Microsoft.App/managedEnvironments@2026-07-01' existing = {
  name: existingEnvironmentName
}
resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = {
  name: existingRegistryName
}
resource foundry 'Microsoft.CognitiveServices/accounts@2026-11-01' existing = {
  name: foundryAccountName
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: identityName
  location: location
  tags: tags
}
resource imagePull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, 'AcrPull')
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}
resource inference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, identity.id, 'CognitiveServicesUser')
  scope: foundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}
resource app 'Microsoft.App/containerApps@2026-07-01' = if (deployApp) {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: hosting.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        allowInsecure: false
        targetPort: 8080
        transport: 'http'
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
      secrets: concat([
        { name: 'evaluator-api-key', value: evaluatorApiKey }
      ], empty(evaluatorConfigJson) ? [] : [
        { name: 'evaluator-config', value: evaluatorConfigJson }
      ])
    }
    template: {
      containers: [
        {
          name: 'evaluator'
          image: containerImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: concat([
            { name: 'PORT', value: '8080' }
            { name: 'AZURE_AI_RESOURCE_ENDPOINT', value: foundryEndpoint }
            { name: 'DECISION_DEPLOYMENT', value: 'Decision-1' }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'EVALUATOR_API_KEY', secretRef: 'evaluator-api-key' }
          ], empty(evaluatorConfigJson) ? [] : [
            { name: 'EVALUATORS_CONFIG_JSON', secretRef: 'evaluator-config' }
          ])
          probes: [
            {
              type: 'Readiness'
              httpGet: { path: '/health', port: 8080 }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8080 }
              initialDelaySeconds: 15
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: 1
        rules: [
          { name: 'http', http: { metadata: { concurrentRequests: '5' } } }
        ]
      }
    }
  }
  dependsOn: [imagePull, inference]
}

output endpoint string = deployApp ? 'https://${app!.properties.configuration.ingress.fqdn}' : ''
output appResourceId string = deployApp ? app!.id : ''
output identityResourceId string = identity.id
