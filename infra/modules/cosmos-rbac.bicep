@description('Cosmos DB account name receiving the data-plane role assignment.')
param accountName string

@description('Managed identity principal object ID.')
param principalId string

@description('Built-in Cosmos DB SQL data-plane role to assign.')
@allowed([
  'DataReader'
  'DataContributor'
])
param builtInRole string

@description('Also grant ARM Reader on the Cosmos account for Azure Portal discovery.')
param includeAccountReader bool = false

var roleDefinitionGuid = builtInRole == 'DataContributor'
  ? '00000000-0000-0000-0000-000000000002'
  : '00000000-0000-0000-0000-000000000001'
var accountReaderRoleDefinitionGuid = 'acdd72a7-3385-48ef-bd42-f606fba81ae7'

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: accountName
}

resource roleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: account
  name: guid(account.id, principalId, roleDefinitionGuid)
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${roleDefinitionGuid}'
    principalId: principalId
    scope: account.id
  }
}

resource accountReaderRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (includeAccountReader) {
  name: guid(account.id, principalId, accountReaderRoleDefinitionGuid)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      accountReaderRoleDefinitionGuid
    )
    principalId: principalId
  }
}
