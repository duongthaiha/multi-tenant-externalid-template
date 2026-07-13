param environmentName string
param location string
param tags object
param tenantNames array
param privateEndpointSubnetId string
param privateDnsZoneId string
param portfolioAgentPrincipalId string = ''

@description('Optional portfolio-agent-python identity principal ID to grant Cosmos Data Contributor access to the same shared agent-memory account/container.')
param portfolioAgentPythonPrincipalId string = ''

var normalizedEnvironment = toLower(replace(environmentName, '-', ''))
var accountName = 'cosmos-${take(normalizedEnvironment, 8)}-agentmem-${take(uniqueString(resourceGroup().id), 8)}'
var containerName = 'agentSessions'
var databasePrefix = 'agent-memory-'
var pythonDatabasePrefix = 'agent-memory-python-'
var databaseNames = [for tenantName in tenantNames: '${databasePrefix}${toLower(replace(tenantName, '-', ''))}']
var pythonDatabaseNames = [for tenantName in tenantNames: '${pythonDatabasePrefix}${toLower(replace(tenantName, '-', ''))}']
var dataContributorRoleDefinitionId = '00000000-0000-0000-0000-000000000002'

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: accountName
  location: location
  tags: union(tags, {
    dataPlane: 'agent-memory'
  })
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    disableLocalAuth: true
    publicNetworkAccess: 'Disabled'
    networkAclBypass: 'None'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
  }
}

resource databases 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = [for databaseName in databaseNames: {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}]

resource sessions 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = [for (databaseName, i) in databaseNames: {
  parent: databases[i]
  name: containerName
  properties: {
    resource: {
      id: containerName
      defaultTtl: 2592000
      partitionKey: {
        paths: [
          '/tenantId'
        ]
        kind: 'Hash'
      }
    }
  }
}]

resource pythonDatabases 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = [for databaseName in pythonDatabaseNames: {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}]

resource pythonSessions 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = [for (databaseName, i) in pythonDatabaseNames: {
  parent: pythonDatabases[i]
  name: containerName
  properties: {
    resource: {
      id: containerName
      defaultTtl: 2592000
      partitionKey: {
        paths: [
          '/tenantId'
        ]
        kind: 'Hash'
      }
    }
  }
}]

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-${account.name}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'cosmos-sql'
        properties: {
          privateLinkServiceId: account.id
          groupIds: [
            'Sql'
          ]
        }
      }
    ]
  }
}

resource dnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'cosmos'
        properties: {
          privateDnsZoneId: privateDnsZoneId
        }
      }
    ]
  }
}

resource roleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = if (!empty(portfolioAgentPrincipalId)) {
  parent: account
  name: guid(account.id, portfolioAgentPrincipalId, dataContributorRoleDefinitionId)
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributorRoleDefinitionId}'
    principalId: portfolioAgentPrincipalId
    scope: account.id
  }
}

resource roleAssignmentPython 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = if (!empty(portfolioAgentPythonPrincipalId)) {
  parent: account
  name: guid(account.id, portfolioAgentPythonPrincipalId, dataContributorRoleDefinitionId)
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributorRoleDefinitionId}'
    principalId: portfolioAgentPythonPrincipalId
    scope: account.id
  }
}

output accountName string = account.name
output endpoint string = account.properties.documentEndpoint
output containerName string = containerName
output databasePrefix string = databasePrefix
output pythonDatabasePrefix string = pythonDatabasePrefix
