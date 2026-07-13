param environmentName string
param location string
param tags object

resource appConfig 'Microsoft.AppConfiguration/configurationStores@2024-05-01' = {
  name: 'appcs-${environmentName}-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  sku: {
    name: 'standard'
  }
  properties: {
    disableLocalAuth: true
    publicNetworkAccess: 'Disabled'
  }
}

output name string = appConfig.name
output id string = appConfig.id
output endpoint string = appConfig.properties.endpoint
