param environmentName string
param location string
param tags object
param alertActionGroupEmail string

var tenantMismatchAlertQuery = '''
union isfuzzy=true
  (AppTraces
    | where Message has "tenant-mismatch" or tostring(Properties.authorizationDecision) == "tenant-mismatch"
    | where tostring(Properties.statusCode) == "403" or tostring(Properties.StatusCode) == "403"
    | project TimeGenerated),
  (AzureDiagnostics
    | where Category == "GatewayLogs"
    | where toint(column_ifexists("ResponseCode", 0)) == 403
    | where tostring(column_ifexists("responseHeaders_s", "")) has "tenant-mismatch"
    | project TimeGenerated)
| summarize Count=count()
'''

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${environmentName}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${environmentName}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-${environmentName}-tenant-mismatch'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'tenant403'
    enabled: true
    emailReceivers: [
      {
        name: 'tenant-mismatch-email'
        emailAddress: alertActionGroupEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

resource tenantMismatchAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'alert-${environmentName}-tenant-mismatch-403'
  location: location
  tags: tags
  properties: {
    displayName: 'Tenant mismatch 403 spike'
    description: 'Fires when more than five tenant-mismatch 403 responses occur in five minutes.'
    enabled: true
    scopes: [
      workspace.id
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    severity: 2
    criteria: {
      allOf: [
        {
          query: tenantMismatchAlertQuery
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 5
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

output logAnalyticsWorkspaceId string = workspace.id
output logAnalyticsCustomerId string = workspace.properties.customerId
@secure()
output logAnalyticsSharedKey string = workspace.listKeys().primarySharedKey
output applicationInsightsResourceId string = appInsights.id
output applicationInsightsConnectionString string = appInsights.properties.ConnectionString
