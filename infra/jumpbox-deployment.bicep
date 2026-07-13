targetScope = 'resourceGroup'

param environmentName string
param location string = resourceGroup().location
param keyVaultName string
param operatorPrincipalId string

@allowed([
  'User'
  'Group'
])
param operatorPrincipalType string = 'User'

param cosmosAccountNames array
param vmSize string = 'Standard_D2als_v7'
param adminUsername string = 'jumpboxadmin'
param shutdownTime string = '1900'
param shutdownTimeZone string = 'UTC'

var tags = {
  application: 'contoso-asset-management'
  environment: environmentName
  workload: 'multi-tenant-poc'
  'azd-env-name': environmentName
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: 'vnet-${environmentName}'
}

resource jumpboxSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  parent: vnet
  name: 'snet-jumpbox'
  properties: {
    addressPrefix: '10.40.4.0/27'
  }
}

resource bastionSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  parent: vnet
  name: 'AzureBastionSubnet'
  properties: {
    addressPrefix: '10.40.5.0/26'
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

module jumpbox 'modules/jumpbox.bicep' = {
  name: 'jumpbox-resources'
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    jumpboxSubnetId: jumpboxSubnet.id
    bastionSubnetId: bastionSubnet.id
    bastionSubnetAddressPrefix: bastionSubnet.properties.addressPrefix
    operatorPrincipalId: operatorPrincipalId
    operatorPrincipalType: operatorPrincipalType
    vmSize: vmSize
    adminUsername: adminUsername
    adminPassword: keyVault.getSecret('jumpbox-local-admin-password')
    shutdownTime: shutdownTime
    shutdownTimeZone: shutdownTimeZone
  }
}

module cosmosReaders 'modules/cosmos-rbac.bicep' = [for (accountName, i) in cosmosAccountNames: {
  name: 'rbac-jumpbox-reader-${i}'
  params: {
    accountName: accountName
    principalId: operatorPrincipalId
    builtInRole: 'DataReader'
    includeAccountReader: true
  }
}]

output jumpboxVmName string = jumpbox.outputs.vmName
output bastionName string = jumpbox.outputs.bastionName
output jumpboxPrivateIpAddress string = jumpbox.outputs.privateIpAddress
