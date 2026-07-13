targetScope = 'resourceGroup'

param keyVaultName string

@secure()
param adminPassword string

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource jumpboxAdminPassword 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'jumpbox-local-admin-password'
  properties: {
    value: adminPassword
    contentType: 'Windows jumpbox emergency local administrator password'
  }
}
