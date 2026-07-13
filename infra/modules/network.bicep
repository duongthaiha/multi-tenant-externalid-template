param environmentName string
param location string
param tags object

var vnetName = 'vnet-${environmentName}'
var appsSubnetName = 'snet-apps'
var functionsSubnetName = 'snet-func'
var privateEndpointSubnetName = 'snet-pe'
var jumpboxSubnetName = 'snet-jumpbox'
var bastionSubnetName = 'AzureBastionSubnet'

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.40.0.0/16'
      ]
    }
    subnets: [
      {
        name: appsSubnetName
        properties: {
          addressPrefix: '10.40.1.0/24'
          delegations: [
            {
              name: 'container-apps-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: functionsSubnetName
        properties: {
          addressPrefix: '10.40.2.0/24'
          delegations: [
            {
              name: 'functions-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: privateEndpointSubnetName
        properties: {
          addressPrefix: '10.40.3.0/24'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: jumpboxSubnetName
        properties: {
          addressPrefix: '10.40.4.0/27'
        }
      }
      {
        name: bastionSubnetName
        properties: {
          addressPrefix: '10.40.5.0/26'
        }
      }
    ]
  }
}

resource cosmosPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.documents.azure.com'
  location: 'global'
  tags: tags
}

resource cosmosPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: cosmosPrivateDnsZone
  name: '${vnetName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource storageBlobPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.blob.core.windows.net'
  location: 'global'
  tags: tags
}

resource storageBlobPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: storageBlobPrivateDnsZone
  name: '${vnetName}-blob-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource storageQueuePrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.queue.core.windows.net'
  location: 'global'
  tags: tags
}

resource storageQueuePrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: storageQueuePrivateDnsZone
  name: '${vnetName}-queue-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource storageTablePrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.table.core.windows.net'
  location: 'global'
  tags: tags
}

resource storageTablePrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: storageTablePrivateDnsZone
  name: '${vnetName}-table-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

output containerAppsSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, appsSubnetName)
output functionsSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, functionsSubnetName)
output privateEndpointSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, privateEndpointSubnetName)
output jumpboxSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, jumpboxSubnetName)
output bastionSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, bastionSubnetName)
output bastionSubnetAddressPrefix string = '10.40.5.0/26'
output cosmosPrivateDnsZoneId string = cosmosPrivateDnsZone.id
output storageBlobPrivateDnsZoneId string = storageBlobPrivateDnsZone.id
output storageQueuePrivateDnsZoneId string = storageQueuePrivateDnsZone.id
output storageTablePrivateDnsZoneId string = storageTablePrivateDnsZone.id
