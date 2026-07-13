param environmentName string
param location string
param tags object

var normalizedEnvironment = toLower(replace(environmentName, '-', ''))

resource staticWebApp 'Microsoft.Web/staticSites@2023-12-01' = {
  name: 'stapp-${take(normalizedEnvironment, 12)}-${take(uniqueString(resourceGroup().id), 8)}'
  location: location
  tags: union(tags, {
    'azd-service-name': 'spa'
  })
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    repositoryUrl: ''
    branch: ''
    buildProperties: {
      appLocation: '/'
      outputLocation: 'dist'
    }
  }
}

output name string = staticWebApp.name
output defaultHostname string = staticWebApp.properties.defaultHostname
output url string = 'https://${staticWebApp.properties.defaultHostname}'
