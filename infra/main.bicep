targetScope = 'subscription'

@description('Short environment name used for resource naming.')
param environmentName string

@description('Azure region for all POC resources.')
param location string = deployment().location

@description('Business tenants provisioned during the initial deployment.')
param tenantNames array = [
  'AlphaCapital'
  'BetaWealth'
  'GammaFund'
]

@description('Expected Azure External ID issuer.')
param externalIdIssuer string

@description('Expected API audience/application ID URI.')
param apiAudience string

@description('Frontend API application/client ID. Used to resolve app-specific token-signing metadata for mapped claims.')
param frontendApiClientId string

@description('Azure External ID authority used for OpenID Connect metadata.')
param externalIdAuthority string

@description('Internal Entra authority used for backend service-to-service tokens.')
param backendServiceAuthority string

@description('Internal Entra issuer used for backend service-to-service tokens.')
param backendServiceIssuer string

@description('Backend API audience/application ID URI for internal Entra service-to-service tokens.')
param backendApiAudience string

@description('Backend API /.default scope requested from internal Entra by the frontend API managed identity.')
param backendApiServiceTokenScope string

@description('APIM publisher email.')
param apimPublisherEmail string

@description('APIM publisher name.')
param apimPublisherName string

@description('Email target for tenant-mismatch alert action group.')
param alertActionGroupEmail string

@description('Azure region for AI Foundry hosted-agent resources. Use a region supported by Foundry hosted agents.')
param foundryLocation string = 'eastus2'

@description('Foundry model deployment name used by the portfolio hosted agent.')
param foundryModelDeploymentName string = 'gpt-4.1-mini'

@description('Foundry model catalog name used by the portfolio hosted agent.')
param foundryModelName string = 'gpt-4.1-mini'

@description('Foundry model version used by the portfolio hosted agent.')
param foundryModelVersion string = '2025-04-14'

@description('Foundry model deployment SKU name. Choose a SKU with quota in foundryLocation.')
param foundryModelSkuName string = 'GlobalStandard'

@description('Foundry model deployment capacity.')
param foundryModelCapacity int = 10

@description('Portfolio hosted-agent OpenAI-compatible responses endpoint used by the frontend API/BFF.')
param portfolioAgentResponsesEndpoint string = ''

@description('Portfolio hosted-agent custom invocations endpoint used by the frontend API/BFF.')
param portfolioAgentInvocationsEndpoint string = ''

@description('Optional portfolio-agent (C#) identity principal ID to grant access to the agent-memory Cosmos account. Retained for backward compatibility.')
param portfolioAgentPrincipalId string = ''

@description('Optional portfolio-agent-python identity principal ID to grant access to the shared agent-memory Cosmos account.')
param portfolioAgentPythonPrincipalId string = ''

@description('Optional principal ID to grant Azure AI User on the Foundry project.')
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

@description('Deploy an optional Windows jumpbox and Azure Bastion for private Cosmos Data Explorer access.')
@allowed([
  'true'
  'false'
])
param enableJumpbox string = 'false'

@description('Microsoft Entra object ID of the user or group allowed to sign in to the jumpbox and read Cosmos data.')
param jumpboxUserPrincipalId string = ''

@description('Principal type for jumpboxUserPrincipalId.')
@allowed([
  'User'
  'Group'
])
param jumpboxUserPrincipalType string = 'User'

@description('Azure VM size for the Windows jumpbox.')
param jumpboxVmSize string = 'Standard_D2als_v7'

@description('Emergency local administrator username. Daily access uses Microsoft Entra ID.')
param jumpboxAdminUsername string = 'jumpboxadmin'

@description('Nightly shutdown time in HHmm format.')
param jumpboxShutdownTime string = '1900'

@description('Windows time zone used by the nightly shutdown schedule.')
param jumpboxShutdownTimeZone string = 'UTC'

var suffix = uniqueString(subscription().id, environmentName, location)
var jumpboxEnabled = enableJumpbox == 'true'
var resourceGroupName = 'rg-${environmentName}-${suffix}'
var tags = {
  application: 'contoso-asset-management'
  environment: environmentName
  workload: 'multi-tenant-poc'
  'azd-env-name': environmentName
}
var foundryUserRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    alertActionGroupEmail: alertActionGroupEmail
  }
}

module network 'modules/network.bicep' = {
  name: 'network'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
  }
}

module keyVault 'modules/key-vault.bicep' = {
  name: 'key-vault'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
  }
}

module appConfiguration 'modules/app-configuration.bicep' = {
  name: 'app-configuration'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
  }
}

module containerRegistry 'modules/container-registry.bicep' = {
  name: 'container-registry'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
  }
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  scope: rg
  params: {
    environmentName: environmentName
    location: foundryLocation
    tags: tags
    applicationInsightsConnectionString: monitoring.outputs.applicationInsightsConnectionString
    applicationInsightsResourceId: monitoring.outputs.applicationInsightsResourceId
    containerRegistryName: containerRegistry.outputs.name
    containerRegistryId: containerRegistry.outputs.id
    containerRegistryEndpoint: containerRegistry.outputs.loginServer
    modelDeploymentName: foundryModelDeploymentName
    modelName: foundryModelName
    modelVersion: foundryModelVersion
    modelSkuName: foundryModelSkuName
    modelCapacity: foundryModelCapacity
    developerPrincipalId: developerPrincipalId
    developerPrincipalType: developerPrincipalType
  }
}

module controlPlaneCosmos 'modules/cosmos-control-plane.bicep' = {
  name: 'cosmos-control-plane'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    privateEndpointSubnetId: network.outputs.privateEndpointSubnetId
    privateDnsZoneId: network.outputs.cosmosPrivateDnsZoneId
  }
}

module tenantCosmos 'modules/cosmos-tenant.bicep' = [for tenantName in tenantNames: {
  name: 'cosmos-${toLower(tenantName)}'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    tenantName: tenantName
    privateEndpointSubnetId: network.outputs.privateEndpointSubnetId
    privateDnsZoneId: network.outputs.cosmosPrivateDnsZoneId
  }
}]

module agentMemoryCosmos 'modules/cosmos-agent-memory.bicep' = {
  name: 'cosmos-agent-memory'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    tenantNames: tenantNames
    privateEndpointSubnetId: network.outputs.privateEndpointSubnetId
    privateDnsZoneId: network.outputs.cosmosPrivateDnsZoneId
    portfolioAgentPrincipalId: portfolioAgentPrincipalId
    portfolioAgentPythonPrincipalId: portfolioAgentPythonPrincipalId
  }
}

module bffSessionBindingsCosmos 'modules/cosmos-bff-session-bindings.bicep' = {
  name: 'cosmos-bff-session-bindings'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    privateEndpointSubnetId: network.outputs.privateEndpointSubnetId
    privateDnsZoneId: network.outputs.cosmosPrivateDnsZoneId
  }
}

resource jumpboxKeyVaultSecretSource 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVault.outputs.name
  scope: rg
}

module jumpbox 'modules/jumpbox.bicep' = if (jumpboxEnabled) {
  name: 'jumpbox'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    jumpboxSubnetId: network.outputs.jumpboxSubnetId
    bastionSubnetId: network.outputs.bastionSubnetId
    bastionSubnetAddressPrefix: network.outputs.bastionSubnetAddressPrefix
    operatorPrincipalId: jumpboxUserPrincipalId
    operatorPrincipalType: jumpboxUserPrincipalType
    vmSize: jumpboxVmSize
    adminUsername: jumpboxAdminUsername
    adminPassword: jumpboxKeyVaultSecretSource.getSecret('jumpbox-local-admin-password')
    shutdownTime: jumpboxShutdownTime
    shutdownTimeZone: jumpboxShutdownTimeZone
  }
}

module containerApps 'modules/container-apps.bicep' = {
  name: 'container-apps'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    containerAppsSubnetId: network.outputs.containerAppsSubnetId
    logAnalyticsCustomerId: monitoring.outputs.logAnalyticsCustomerId
    logAnalyticsSharedKey: monitoring.outputs.logAnalyticsSharedKey
    appConfigurationEndpoint: appConfiguration.outputs.endpoint
    applicationInsightsConnectionString: monitoring.outputs.applicationInsightsConnectionString
    controlPlaneCosmosEndpoint: controlPlaneCosmos.outputs.endpoint
    agentSessionBindingCosmosEndpoint: bffSessionBindingsCosmos.outputs.endpoint
    agentSessionBindingCosmosDatabaseName: bffSessionBindingsCosmos.outputs.databaseName
    agentSessionBindingCosmosContainerName: bffSessionBindingsCosmos.outputs.containerName
    apiAudience: apiAudience
    frontendApiClientId: frontendApiClientId
    externalIdAuthority: externalIdAuthority
    externalIdIssuer: externalIdIssuer
    backendServiceAuthority: backendServiceAuthority
    backendServiceIssuer: backendServiceIssuer
    backendApiAudience: backendApiAudience
    backendApiServiceTokenScope: backendApiServiceTokenScope
    portfolioAgentResponsesEndpoint: portfolioAgentResponsesEndpoint
    portfolioAgentInvocationsEndpoint: portfolioAgentInvocationsEndpoint
    containerRegistryServer: containerRegistry.outputs.loginServer
    tenantNames: tenantNames
  }
}

module frontendFoundryUser 'modules/foundry-project-rbac.bicep' = {
  name: 'rbac-foundry-user-frontend'
  scope: rg
  params: {
    accountName: foundry.outputs.accountName
    projectName: foundry.outputs.projectName
    roleDefinitionId: foundryUserRoleDefinitionId
    principalId: containerApps.outputs.frontendPrincipalId
    principalType: 'ServicePrincipal'
  }
}

module functions 'modules/functions.bicep' = {
  name: 'functions'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    functionsSubnetId: network.outputs.functionsSubnetId
    privateEndpointSubnetId: network.outputs.privateEndpointSubnetId
    storageBlobPrivateDnsZoneId: network.outputs.storageBlobPrivateDnsZoneId
    storageQueuePrivateDnsZoneId: network.outputs.storageQueuePrivateDnsZoneId
    storageTablePrivateDnsZoneId: network.outputs.storageTablePrivateDnsZoneId
    applicationInsightsConnectionString: monitoring.outputs.applicationInsightsConnectionString
    controlPlaneCosmosEndpoint: controlPlaneCosmos.outputs.endpoint
  }
}

module staticWebApp 'modules/static-web-app.bicep' = {
  name: 'static-web-app'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
  }
}

module apim 'modules/apim.bicep' = {
  name: 'apim'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    publisherEmail: apimPublisherEmail
    publisherName: apimPublisherName
    frontendApiUrl: '${containerApps.outputs.frontendApiUrl}/api'
    backendApiUrl: containerApps.outputs.backendApiUrl
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    externalIdIssuer: externalIdIssuer
    apiAudience: apiAudience
    frontendApiClientId: frontendApiClientId
    backendApiAudience: backendApiAudience
    mcpGatewayIdentityResourceId: containerApps.outputs.mcpGatewayIdentityResourceId
    mcpGatewayIdentityClientId: containerApps.outputs.mcpGatewayClientId
    allowedCorsOrigins: [
      staticWebApp.outputs.url
      'http://127.0.0.1:5173'
      'http://localhost:5173'
    ]
  }
}

module functionControlPlaneCosmosReader 'modules/cosmos-rbac.bicep' = {
  name: 'rbac-control-cosmos-function-reader'
  scope: rg
  params: {
    accountName: controlPlaneCosmos.outputs.accountName
    principalId: functions.outputs.functionPrincipalId
    builtInRole: 'DataReader'
  }
}

module backendControlPlaneCosmosReader 'modules/cosmos-rbac.bicep' = {
  name: 'rbac-control-cosmos-backend-reader'
  scope: rg
  params: {
    accountName: controlPlaneCosmos.outputs.accountName
    principalId: containerApps.outputs.backendPrincipalId
    builtInRole: 'DataReader'
  }
}

module backendTenantCosmosContributors 'modules/cosmos-rbac.bicep' = [for (tenantName, i) in tenantNames: {
  name: 'rbac-tenant-cosmos-${toLower(tenantName)}-backend'
  scope: rg
  params: {
    accountName: tenantCosmos[i].outputs.accountName
    principalId: containerApps.outputs.tenantCosmosIdentityClientIds[i].principalId
    builtInRole: 'DataContributor'
  }
}]

module frontendBffSessionBindingsCosmosContributor 'modules/cosmos-rbac.bicep' = {
  name: 'rbac-bff-session-bindings-frontend'
  scope: rg
  params: {
    accountName: bffSessionBindingsCosmos.outputs.accountName
    principalId: containerApps.outputs.frontendPrincipalId
    builtInRole: 'DataContributor'
  }
}

module jumpboxControlPlaneCosmosReader 'modules/cosmos-rbac.bicep' = if (jumpboxEnabled) {
  name: 'rbac-control-cosmos-jumpbox-reader'
  scope: rg
  params: {
    accountName: controlPlaneCosmos.outputs.accountName
    principalId: jumpboxUserPrincipalId
    builtInRole: 'DataReader'
    includeAccountReader: true
  }
}

module jumpboxTenantCosmosReaders 'modules/cosmos-rbac.bicep' = [for (tenantName, i) in tenantNames: if (jumpboxEnabled) {
  name: 'rbac-tenant-cosmos-${toLower(tenantName)}-jumpbox-reader'
  scope: rg
  params: {
    accountName: tenantCosmos[i].outputs.accountName
    principalId: jumpboxUserPrincipalId
    builtInRole: 'DataReader'
    includeAccountReader: true
  }
}]

module jumpboxAgentMemoryCosmosReader 'modules/cosmos-rbac.bicep' = if (jumpboxEnabled) {
  name: 'rbac-agent-memory-cosmos-jumpbox-reader'
  scope: rg
  params: {
    accountName: agentMemoryCosmos.outputs.accountName
    principalId: jumpboxUserPrincipalId
    builtInRole: 'DataReader'
    includeAccountReader: true
  }
}

module jumpboxBffSessionBindingsCosmosReader 'modules/cosmos-rbac.bicep' = if (jumpboxEnabled) {
  name: 'rbac-bff-session-cosmos-jumpbox-reader'
  scope: rg
  params: {
    accountName: bffSessionBindingsCosmos.outputs.accountName
    principalId: jumpboxUserPrincipalId
    builtInRole: 'DataReader'
    includeAccountReader: true
  }
}

module appConfigurationRbac 'modules/app-configuration-rbac.bicep' = {
  name: 'rbac-app-configuration'
  scope: rg
  params: {
    appConfigurationName: appConfiguration.outputs.name
    principalIds: [
      containerApps.outputs.frontendPrincipalId
      containerApps.outputs.backendPrincipalId
    ]
  }
}

module keyVaultRbac 'modules/key-vault-rbac.bicep' = {
  name: 'rbac-key-vault'
  scope: rg
  params: {
    keyVaultName: keyVault.outputs.name
    principalIds: [
      containerApps.outputs.frontendPrincipalId
      containerApps.outputs.backendPrincipalId
      functions.outputs.functionPrincipalId
    ]
  }
}

module containerRegistryRbac 'modules/container-registry-rbac.bicep' = {
  name: 'rbac-container-registry'
  scope: rg
  params: {
    registryName: containerRegistry.outputs.name
    principalIds: [
      containerApps.outputs.frontendPrincipalId
      containerApps.outputs.backendPrincipalId
    ]
  }
}

output resourceGroupName string = rg.name
output AZURE_RESOURCE_GROUP string = rg.name
output apimGatewayUrl string = apim.outputs.gatewayUrl
output frontendApiUrl string = containerApps.outputs.frontendApiUrl
output BACKEND_API_BASE_URL string = containerApps.outputs.backendApiUrl
output BACKEND_MCP_SERVER_URL string = '${apim.outputs.gatewayUrl}/backend-assets-mcp/mcp'
output AGENT_MEMORY_ENDPOINT string = agentMemoryCosmos.outputs.endpoint
output AGENT_MEMORY_DATABASE_PREFIX string = agentMemoryCosmos.outputs.databasePrefix
output AGENT_MEMORY_PYTHON_DATABASE_PREFIX string = agentMemoryCosmos.outputs.pythonDatabasePrefix
output AGENT_MEMORY_CONTAINER_NAME string = agentMemoryCosmos.outputs.containerName
output backendApiFqdn string = containerApps.outputs.backendApiFqdn
output mcpGatewayClientId string = containerApps.outputs.mcpGatewayClientId
output customClaimsProviderName string = functions.outputs.functionAppName
output spaUrl string = staticWebApp.outputs.url
output jumpboxVmName string = jumpboxEnabled ? jumpbox!.outputs.vmName : ''
output bastionName string = jumpboxEnabled ? jumpbox!.outputs.bastionName : ''
output containerRegistryName string = containerRegistry.outputs.name
output AZURE_CONTAINER_REGISTRY_NAME string = containerRegistry.outputs.name
output AZURE_CONTAINER_REGISTRY_RESOURCE_ID string = containerRegistry.outputs.id
output containerRegistryEndpoint string = containerRegistry.outputs.loginServer
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistry.outputs.loginServer
output AZURE_AI_ACCOUNT_ID string = foundry.outputs.accountId
output AZURE_AI_ACCOUNT_NAME string = foundry.outputs.accountName
output AZURE_AI_PROJECT_ID string = foundry.outputs.projectId
output AZURE_AI_FOUNDRY_PROJECT_ID string = foundry.outputs.projectId
output AZURE_AI_PROJECT_NAME string = foundry.outputs.projectName
output AZURE_AI_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output AZURE_OPENAI_ENDPOINT string = foundry.outputs.openAiEndpoint
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = foundry.outputs.modelDeploymentName
output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.applicationInsightsConnectionString
output APPLICATIONINSIGHTS_RESOURCE_ID string = monitoring.outputs.applicationInsightsResourceId
output AZURE_AI_PROJECT_ACR_CONNECTION_NAME string = foundry.outputs.acrConnectionName
output APPLICATIONINSIGHTS_CONNECTION_NAME string = foundry.outputs.appInsightsConnectionName
