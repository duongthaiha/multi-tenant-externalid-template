@description('Key Vault name receiving secret reader assignments.')
param keyVaultName string

@description('Managed identity principal object IDs that can read secrets.')
param principalIds array

var keyVaultSecretsUserRoleDefinitionId = '4633458b-17de-408a-b874-0445c86b69e6'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource assignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principalId in principalIds: {
  scope: keyVault
  name: guid(keyVault.id, principalId, keyVaultSecretsUserRoleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}]
