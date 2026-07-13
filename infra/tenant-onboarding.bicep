targetScope = 'resourceGroup'

@description('Short environment name used for resource naming.')
param environmentName string

@description('Azure region for the tenant resources.')
param location string = resourceGroup().location

@description('Business tenant to onboard.')
param tenantName string = 'DeltaEquity'

@description('Optional principal object ID that can seed data with Cosmos DB SQL Data Contributor. Leave empty when the runner already has data-plane RBAC.')
param seedPrincipalId string = ''

var tags = {
  application: 'contoso-asset-management'
  environment: environmentName
  workload: 'multi-tenant-poc'
}

resource backendApi 'Microsoft.App/containerApps@2024-03-01' existing = {
  name: 'ca-${environmentName}-backend-api'
}

var privateEndpointSubnetId = resourceId('Microsoft.Network/virtualNetworks/subnets', 'vnet-${environmentName}', 'snet-pe')
var cosmosPrivateDnsZoneId = resourceId('Microsoft.Network/privateDnsZones', 'privatelink.documents.azure.com')

module tenantCosmos 'modules/cosmos-tenant.bicep' = {
  name: 'cosmos-${toLower(tenantName)}'
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    tenantName: tenantName
    privateEndpointSubnetId: privateEndpointSubnetId
    privateDnsZoneId: cosmosPrivateDnsZoneId
  }
}

module backendTenantCosmosContributor 'modules/cosmos-rbac.bicep' = {
  name: 'rbac-tenant-cosmos-${toLower(tenantName)}-backend'
  params: {
    accountName: tenantCosmos.outputs.accountName
    principalId: backendApi.identity.principalId
    builtInRole: 'DataContributor'
  }
}

module seedPrincipalTenantCosmosContributor 'modules/cosmos-rbac.bicep' = if (!empty(seedPrincipalId)) {
  name: 'rbac-tenant-cosmos-${toLower(tenantName)}-seed-principal'
  params: {
    accountName: tenantCosmos.outputs.accountName
    principalId: seedPrincipalId
    builtInRole: 'DataContributor'
  }
}

output tenantId string = tenantName
output tenantCosmosAccountName string = tenantCosmos.outputs.accountName
output tenantCosmosEndpoint string = tenantCosmos.outputs.endpoint
output tenantDatabaseName string = tenantCosmos.outputs.databaseName
output backendPrincipalId string = backendApi.identity.principalId
