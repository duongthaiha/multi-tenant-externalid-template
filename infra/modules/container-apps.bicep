param environmentName string
param location string
param tags object
param containerAppsSubnetId string
param logAnalyticsCustomerId string
@secure()
param logAnalyticsSharedKey string
param appConfigurationEndpoint string
param applicationInsightsConnectionString string
param controlPlaneCosmosEndpoint string
param agentSessionBindingCosmosEndpoint string
param agentSessionBindingCosmosDatabaseName string
param agentSessionBindingCosmosContainerName string
param apiAudience string
param frontendApiClientId string
param externalIdAuthority string
param externalIdIssuer string
param backendServiceAuthority string
param backendServiceIssuer string
param backendApiAudience string
param backendApiServiceTokenScope string
param portfolioAgentResponsesEndpoint string
param portfolioAgentInvocationsEndpoint string
param containerRegistryServer string
param tenantNames array

var placeholderImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var normalizedTenantNames = [for tenantName in tenantNames: toLower(replace(tenantName, '-', ''))]

resource frontendServiceIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${environmentName}-frontend-api'
  location: location
  tags: tags
}

resource mcpGatewayIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${environmentName}-mcp-gateway'
  location: location
  tags: union(tags, {
    identityPurpose: 'apim-mcp-backend-service-auth'
  })
}

resource tenantDataIdentities 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for (tenantName, i) in tenantNames: {
  name: 'id-${environmentName}-${normalizedTenantNames[i]}-cosmos'
  location: location
  tags: union(tags, {
    tenantId: tenantName
    identityPurpose: 'tenant-cosmos-data'
  })
}]

var backendUserAssignedIdentityItems = [for (tenantName, i) in tenantNames: {
  id: tenantDataIdentities[i].id
}]
var backendUserAssignedIdentities = toObject(backendUserAssignedIdentityItems, item => item.id, item => {})

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${environmentName}'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: containerAppsSubnetId
      internal: false
    }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
  }
}

resource backendApi 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${environmentName}-backend-api'
  location: location
  tags: union(tags, {
    'azd-service-name': 'backend-api'
  })
  identity: {
    type: 'SystemAssigned,UserAssigned'
    userAssignedIdentities: backendUserAssignedIdentities
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: containerRegistryServer
          identity: 'system'
        }
      ]
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
      }
    }
    template: {
      containers: [
        {
          name: 'backend-api'
          image: placeholderImage
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: applicationInsightsConnectionString
            }
            {
              name: 'AppConfiguration__Endpoint'
              value: appConfigurationEndpoint
            }
            {
              name: 'ControlPlane__Endpoint'
              value: controlPlaneCosmosEndpoint
            }
            {
              name: 'Auth__Authority'
              value: '${externalIdAuthority}/v2.0'
            }
            {
              name: 'Auth__MetadataAddress'
              value: '${externalIdAuthority}/v2.0/.well-known/openid-configuration?appid=${frontendApiClientId}'
            }
            {
              name: 'Auth__Issuer'
              value: externalIdIssuer
            }
            {
              name: 'Auth__Audience'
              value: apiAudience
            }
            {
              name: 'ServiceAuth__Authority'
              value: backendServiceAuthority
            }
            {
              name: 'ServiceAuth__MetadataAddress'
              value: '${backendServiceAuthority}/v2.0/.well-known/openid-configuration'
            }
            {
              name: 'ServiceAuth__Issuer'
              value: backendServiceIssuer
            }
            {
              name: 'ServiceAuth__AdditionalIssuers__0'
              value: '${backendServiceAuthority}/v2.0'
            }
            {
              name: 'ServiceAuth__Audience'
              value: backendApiAudience
            }
            {
              name: 'ServiceAuth__ReadRoles__0'
              value: 'Backend.Read'
            }
            {
              name: 'ServiceAuth__ReadRoles__1'
              value: 'Backend.Write'
            }
            {
              name: 'ServiceAuth__WriteRoles__0'
              value: 'Backend.Write'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

resource frontendApi 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${environmentName}-frontend-api'
  location: location
  tags: union(tags, {
    'azd-service-name': 'frontend-api'
  })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${frontendServiceIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: containerRegistryServer
          identity: frontendServiceIdentity.id
        }
      ]
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
      }
    }
    template: {
      containers: [
        {
          name: 'frontend-api'
          image: placeholderImage
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: applicationInsightsConnectionString
            }
            {
              name: 'AppConfiguration__Endpoint'
              value: appConfigurationEndpoint
            }
            {
              name: 'BackendApi__BaseAddress'
              value: 'https://${backendApi.properties.configuration.ingress.fqdn}'
            }
            {
              name: 'BackendApi__ServiceTokenScopes__0'
              value: backendApiServiceTokenScope
            }
            {
              name: 'PortfolioAgent__ResponsesEndpoint'
              value: portfolioAgentResponsesEndpoint
            }
            {
              name: 'PortfolioAgent__InvocationsEndpoint'
              value: portfolioAgentInvocationsEndpoint
            }
            {
              name: 'PortfolioAgent__UseInvocations'
              value: 'false'
            }
            {
              name: 'AgentSessionBindingStore__Endpoint'
              value: agentSessionBindingCosmosEndpoint
            }
            {
              name: 'AgentSessionBindingStore__DatabaseName'
              value: agentSessionBindingCosmosDatabaseName
            }
            {
              name: 'AgentSessionBindingStore__ContainerName'
              value: agentSessionBindingCosmosContainerName
            }
            {
              name: 'AgentSessionBindingStore__UseInMemory'
              value: 'false'
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: frontendServiceIdentity.properties.clientId
            }
            {
              name: 'Auth__Authority'
              value: '${externalIdAuthority}/v2.0'
            }
            {
              name: 'Auth__MetadataAddress'
              value: '${externalIdAuthority}/v2.0/.well-known/openid-configuration?appid=${frontendApiClientId}'
            }
            {
              name: 'Auth__Issuer'
              value: externalIdIssuer
            }
            {
              name: 'Auth__Audience'
              value: apiAudience
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output frontendApiUrl string = 'https://${frontendApi.properties.configuration.ingress.fqdn}'
output backendApiUrl string = 'https://${backendApi.properties.configuration.ingress.fqdn}'
output backendApiFqdn string = backendApi.properties.configuration.ingress.fqdn
output frontendPrincipalId string = frontendServiceIdentity.properties.principalId
output frontendClientId string = frontendServiceIdentity.properties.clientId
output mcpGatewayIdentityResourceId string = mcpGatewayIdentity.id
output mcpGatewayPrincipalId string = mcpGatewayIdentity.properties.principalId
output mcpGatewayClientId string = mcpGatewayIdentity.properties.clientId
output backendPrincipalId string = backendApi.identity.principalId
output tenantCosmosIdentityClientIds array = [for (tenantName, i) in tenantNames: {
  tenantId: tenantName
  clientId: tenantDataIdentities[i].properties.clientId
  principalId: tenantDataIdentities[i].properties.principalId
}]
