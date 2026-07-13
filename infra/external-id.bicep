@description('Globally unique CIAM directory resource name. Use a short onmicrosoft.com domain, for example c12345678.onmicrosoft.com.')
param ciamDirectoryName string

@description('Display name for the External ID tenant.')
param displayName string = 'Contoso Asset Management External ID'

@description('Country code for the External ID tenant data residency.')
param countryCode string = 'US'

@description('External ID data residency location.')
@allowed([
  'United States'
  'Europe'
  'Asia Pacific'
  'Australia'
])
param ciamLocation string = 'United States'

@description('Resource tags.')
param tags object = {
  application: 'contoso-asset-management'
  workload: 'multi-tenant-poc'
  component: 'external-id'
}

resource externalIdDirectory 'Microsoft.AzureActiveDirectory/ciamDirectories@2023-05-17-preview' = {
  name: ciamDirectoryName
  location: ciamLocation
  tags: tags
  sku: {
    name: 'Base'
    tier: 'A0'
  }
  properties: {
    createTenantProperties: {
      countryCode: countryCode
      displayName: displayName
    }
  }
}

output ciamDirectoryName string = externalIdDirectory.name
output externalIdTenantId string = externalIdDirectory.properties.tenantId
