param environmentName string
param location string
param tags object

var normalizedEnvironment = toLower(replace(environmentName, '-', ''))

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: 'acr${take(normalizedEnvironment, 12)}${take(uniqueString(resourceGroup().id), 8)}'
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    networkRuleBypassOptions: 'AzureServices'
    policies: {
      quarantinePolicy: {
        status: 'disabled'
      }
      trustPolicy: {
        type: 'Notary'
        status: 'disabled'
      }
      retentionPolicy: {
        days: 7
        status: 'disabled'
      }
    }
  }
}

output name string = registry.name
output id string = registry.id
output loginServer string = registry.properties.loginServer
