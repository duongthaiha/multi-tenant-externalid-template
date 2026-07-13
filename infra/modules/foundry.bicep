param environmentName string
param location string
param tags object
param applicationInsightsConnectionString string
param applicationInsightsResourceId string
param containerRegistryName string
param containerRegistryId string
param containerRegistryEndpoint string

@description('AI Foundry model deployment name used by hosted agents.')
param modelDeploymentName string = 'gpt-4.1-mini'

@description('Model name from the Foundry model catalog.')
param modelName string = 'gpt-4.1-mini'

@description('Model version from the Foundry model catalog.')
param modelVersion string = '2025-04-14'

@description('Model deployment SKU name. Use a SKU with available quota in the selected Foundry region.')
param modelSkuName string = 'GlobalStandard'

@description('Model deployment capacity.')
param modelCapacity int = 10

@description('Optional principal ID to grant Azure AI User on the project.')
param developerPrincipalId string = ''

@description('Principal type for developerPrincipalId.')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
  'ForeignGroup'
  'Device'
])
param developerPrincipalType string = 'User'

var normalizedEnvironment = toLower(replace(environmentName, '-', ''))
var suffix = take(uniqueString(resourceGroup().id, environmentName, location), 8)
var accountName = 'foundry-${take(normalizedEnvironment, 16)}-${suffix}'
var projectName = 'proj-${take(normalizedEnvironment, 20)}'
var appInsightsConnectionName = 'appi-${environmentName}'
var acrConnectionName = 'acr-${environmentName}'
var azureAiUserRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
var acrPullRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    disableLocalAuth: true
    dynamicThrottlingEnabled: false
    publicNetworkAccess: 'Enabled'
    restrictOutboundNetworkAccess: false
    networkAcls: {
      defaultAction: 'Allow'
      virtualNetworkRules: []
      ipRules: []
    }
  }
}

resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundryAccount
  name: modelDeploymentName
  sku: {
    name: modelSkuName
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundryAccount
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: 'Contoso Asset Management portfolio hosted-agent project.'
    displayName: 'Contoso Portfolio Agent'
  }
  dependsOn: [
    modelDeployment
  ]
}

resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: foundryProject
  name: appInsightsConnectionName
  properties: {
    category: 'AppInsights'
    target: applicationInsightsResourceId
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: {
      key: applicationInsightsConnectionString
    }
    metadata: {
      ApiType: 'Azure'
      ResourceId: applicationInsightsResourceId
    }
  }
}

resource acrConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: foundryProject
  name: acrConnectionName
  properties: {
    category: 'ContainerRegistry'
    target: containerRegistryEndpoint
    authType: 'ManagedIdentity'
    isSharedToAll: true
    credentials: {
      clientId: foundryProject.identity.principalId
      resourceId: containerRegistryId
    }
    metadata: {
      ResourceId: containerRegistryId
    }
  }
}

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: containerRegistryName
}

resource foundryProjectAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: containerRegistry
  name: guid(containerRegistryId, foundryProject.id, acrPullRoleDefinitionId)
  properties: {
    principalId: foundryProject.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleDefinitionId
  }
}

resource developerAzureAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(developerPrincipalId)) {
  scope: foundryProject
  name: guid(foundryProject.id, developerPrincipalId, azureAiUserRoleDefinitionId)
  properties: {
    principalId: developerPrincipalId
    principalType: developerPrincipalType
    roleDefinitionId: azureAiUserRoleDefinitionId
  }
}

output accountId string = foundryAccount.id
output accountName string = foundryAccount.name
output projectId string = foundryProject.id
output projectName string = foundryProject.name
output projectEndpoint string = foundryProject.properties.endpoints['AI Foundry API']
output openAiEndpoint string = foundryAccount.properties.endpoints['OpenAI Language Model Instance API']
output modelDeploymentName string = modelDeployment.name
output appInsightsConnectionName string = appInsightsConnection.name
output acrConnectionName string = acrConnection.name
