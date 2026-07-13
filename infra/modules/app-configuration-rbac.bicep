@description('Azure App Configuration store name receiving data reader assignments.')
param appConfigurationName string

@description('Managed identity principal object IDs that can read configuration values.')
param principalIds array

var appConfigurationDataReaderRoleDefinitionId = '516239f1-63e1-4d78-a4de-a74fb236a071'

resource appConfig 'Microsoft.AppConfiguration/configurationStores@2024-05-01' existing = {
  name: appConfigurationName
}

resource assignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principalId in principalIds: {
  scope: appConfig
  name: guid(appConfig.id, principalId, appConfigurationDataReaderRoleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', appConfigurationDataReaderRoleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}]
