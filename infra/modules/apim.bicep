param environmentName string
param location string
param tags object
param publisherEmail string
param publisherName string
param frontendApiUrl string
param backendApiUrl string
param logAnalyticsWorkspaceId string
param externalIdIssuer string
param apiAudience string
param frontendApiClientId string
param backendApiAudience string
param mcpGatewayIdentityResourceId string
param mcpGatewayIdentityClientId string
param allowedCorsOrigins array

var normalizedEnvironment = toLower(replace(environmentName, '-', ''))
var allowedCorsOriginXml = join(map(allowedCorsOrigins, origin => '<origin>${origin}</origin>'), '\n        ')

var commonInboundPolicyTemplate = '''
<policies>
  <inbound>
    <base />
    <cors allow-credentials="false">
      <allowed-origins>
        {{allowedCorsOrigins}}
      </allowed-origins>
      <allowed-methods preflight-result-max-age="300">
        <method>GET</method>
        <method>POST</method>
        <method>OPTIONS</method>
      </allowed-methods>
      <allowed-headers>
        <header>*</header>
      </allowed-headers>
      <expose-headers>
        <header>X-Correlation-ID</header>
        <header>X-Authorization-Decision</header>
      </expose-headers>
    </cors>
    <set-variable name="correlationId" value='@{
      var headerValue = context.Request.Headers.ContainsKey("X-Correlation-ID") ? context.Request.Headers["X-Correlation-ID"][0] : null;
      return string.IsNullOrWhiteSpace(headerValue) ? Guid.NewGuid().ToString("N") : headerValue;
    }' />
    <validate-jwt header-name="Authorization" require-scheme="Bearer" output-token-variable-name="validatedJwt" failed-validation-httpcode="401" failed-validation-error-message="Unauthorized">
      <openid-config url="{{externalIdIssuer}}/.well-known/openid-configuration?appid={{frontendApiClientId}}" />
      <audiences>
        <audience>{{apiAudience}}</audience>
      </audiences>
      <issuers>
        <issuer>{{externalIdIssuer}}</issuer>
      </issuers>
      <required-claims>
        <claim name="extension_tenantId" match="all" />
        <claim name="tenant_status" match="all">
          <value>active</value>
        </claim>
      </required-claims>
    </validate-jwt>
    <set-variable name="tokenTenant" value='@(((Jwt)context.Variables["validatedJwt"]).Claims["extension_tenantId"][0])' />
    <set-variable name="tokenUser" value='@{
      var jwt = (Jwt)context.Variables["validatedJwt"];
      if (jwt.Claims.ContainsKey("oid")) { return jwt.Claims["oid"][0]; }
      if (jwt.Claims.ContainsKey("sub")) { return jwt.Claims["sub"][0]; }
      return "unknown";
    }' />
    <set-header name="X-Tenant-Id" exists-action="override">
      <value>@((string)context.Variables["tokenTenant"])</value>
    </set-header>
    <set-header name="X-User-Id" exists-action="override">
      <value>@((string)context.Variables["tokenUser"])</value>
    </set-header>
    <set-header name="X-Service-Authorization" exists-action="delete" />
    <set-header name="X-Authenticated-Tenant" exists-action="delete" />
    <set-header name="X-Authenticated-User" exists-action="delete" />
    <set-header name="X-Forwarded-User" exists-action="delete" />
    <set-header name="X-Correlation-ID" exists-action="override">
      <value>@((string)context.Variables["correlationId"])</value>
    </set-header>
    <choose>
      <when condition='@(!string.Equals((string)context.Request.MatchedParameters["tenantId"], (string)context.Variables["tokenTenant"], StringComparison.Ordinal))'>
        <return-response>
          <set-status code="403" reason="Forbidden" />
          <set-header name="X-Correlation-ID" exists-action="override">
            <value>@((string)context.Variables["correlationId"])</value>
          </set-header>
          <set-header name="X-Authorization-Decision" exists-action="override">
            <value>tenant-mismatch</value>
          </set-header>
        </return-response>
      </when>
    </choose>
    <rate-limit-by-key calls="100" renewal-period="60" counter-key='@((string)context.Variables["tokenTenant"])' />
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
    <set-header name="X-Correlation-ID" exists-action="override">
      <value>@((string)context.Variables["correlationId"])</value>
    </set-header>
  </outbound>
  <on-error>
    <base />
    <set-header name="X-Correlation-ID" exists-action="override">
      <value>@((string)context.Variables["correlationId"])</value>
    </set-header>
  </on-error>
</policies>
'''

var commonInboundPolicy = replace(replace(replace(replace(commonInboundPolicyTemplate, '{{externalIdIssuer}}', externalIdIssuer), '{{apiAudience}}', apiAudience), '{{frontendApiClientId}}', frontendApiClientId), '{{allowedCorsOrigins}}', allowedCorsOriginXml)

var requireAssetsReadPolicy = '''
<policies>
  <inbound>
    <base />
    <choose>
      <when condition='@{
        var jwt = (Jwt)context.Variables["validatedJwt"];
        if (!jwt.Claims.ContainsKey("scp")) { return true; }
        var scopes = " " + string.Join(" ", jwt.Claims["scp"]) + " ";
        return !scopes.Contains(" assets.read ");
      }'>
        <return-response>
          <set-status code="403" reason="Forbidden" />
          <set-header name="X-Correlation-ID" exists-action="override">
            <value>@((string)context.Variables["correlationId"])</value>
          </set-header>
          <set-header name="X-Authorization-Decision" exists-action="override">
            <value>missing-scope</value>
          </set-header>
        </return-response>
      </when>
    </choose>
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>
'''

var requireAssetsWritePolicy = '''
<policies>
  <inbound>
    <base />
    <choose>
      <when condition='@{
        var jwt = (Jwt)context.Variables["validatedJwt"];
        if (!jwt.Claims.ContainsKey("scp")) { return true; }
        var scopes = " " + string.Join(" ", jwt.Claims["scp"]) + " ";
        return !scopes.Contains(" assets.write ");
      }'>
        <return-response>
          <set-status code="403" reason="Forbidden" />
          <set-header name="X-Correlation-ID" exists-action="override">
            <value>@((string)context.Variables["correlationId"])</value>
          </set-header>
          <set-header name="X-Authorization-Decision" exists-action="override">
            <value>missing-scope</value>
          </set-header>
        </return-response>
      </when>
    </choose>
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>
'''

var mcpInboundPolicyTemplate = '''
<policies>
  <inbound>
    <base />
    <set-variable name="correlationId" value='@{
      var headerValue = context.Request.Headers.ContainsKey("X-Correlation-ID") ? context.Request.Headers["X-Correlation-ID"][0] : null;
      return string.IsNullOrWhiteSpace(headerValue) ? Guid.NewGuid().ToString("N") : headerValue;
    }' />
    <validate-jwt header-name="Authorization" require-scheme="Bearer" output-token-variable-name="validatedJwt" failed-validation-httpcode="401" failed-validation-error-message="Unauthorized">
      <openid-config url="{{externalIdIssuer}}/.well-known/openid-configuration?appid={{frontendApiClientId}}" />
      <audiences>
        <audience>{{apiAudience}}</audience>
      </audiences>
      <issuers>
        <issuer>{{externalIdIssuer}}</issuer>
      </issuers>
      <required-claims>
        <claim name="extension_tenantId" match="all" />
        <claim name="tenant_status" match="all">
          <value>active</value>
        </claim>
      </required-claims>
    </validate-jwt>
    <set-variable name="tokenTenant" value='@(((Jwt)context.Variables["validatedJwt"]).Claims["extension_tenantId"][0])' />
    <set-variable name="tokenUser" value='@{
      var jwt = (Jwt)context.Variables["validatedJwt"];
      if (jwt.Claims.ContainsKey("oid")) { return jwt.Claims["oid"][0]; }
      if (jwt.Claims.ContainsKey("sub")) { return jwt.Claims["sub"][0]; }
      return "unknown";
    }' />
    <set-variable name="mcpAgentKey" value='@{
      var agentId = context.Request.Headers.ContainsKey("X-Agent-Id") ? context.Request.Headers["X-Agent-Id"][0] : "foundry-agent";
      return ((string)context.Variables["tokenTenant"]) + ":" + agentId;
    }' />
    <set-header name="X-Tenant-Id" exists-action="delete" />
    <set-header name="X-User-Id" exists-action="delete" />
    <set-header name="X-Service-Authorization" exists-action="delete" />
    <set-header name="X-Authenticated-Tenant" exists-action="delete" />
    <set-header name="X-Authenticated-User" exists-action="delete" />
    <set-header name="X-Forwarded-User" exists-action="delete" />
    <set-header name="X-Correlation-ID" exists-action="override">
      <value>@((string)context.Variables["correlationId"])</value>
    </set-header>
    <choose>
      <when condition='@(!string.Equals((string)context.Request.MatchedParameters["tenantId"], (string)context.Variables["tokenTenant"], StringComparison.Ordinal))'>
        <return-response>
          <set-status code="403" reason="Forbidden" />
          <set-header name="X-Correlation-ID" exists-action="override">
            <value>@((string)context.Variables["correlationId"])</value>
          </set-header>
          <set-header name="X-Authorization-Decision" exists-action="override">
            <value>tenant-mismatch</value>
          </set-header>
        </return-response>
      </when>
    </choose>
    <rate-limit-by-key calls="30" renewal-period="60" counter-key='@((string)context.Variables["mcpAgentKey"])' />
    <authentication-managed-identity resource="{{backendApiAudience}}" client-id="{{mcpGatewayIdentityClientId}}" output-token-variable-name="backendServiceToken" />
    <set-header name="X-Service-Authorization" exists-action="override">
      <value>@("Bearer " + (string)context.Variables["backendServiceToken"])</value>
    </set-header>
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
    <set-header name="X-Correlation-ID" exists-action="override">
      <value>@((string)context.Variables["correlationId"])</value>
    </set-header>
  </outbound>
  <on-error>
    <base />
    <set-header name="X-Correlation-ID" exists-action="override">
      <value>@((string)context.Variables["correlationId"])</value>
    </set-header>
  </on-error>
</policies>
'''

var mcpInboundPolicy = replace(replace(replace(replace(replace(mcpInboundPolicyTemplate, '{{externalIdIssuer}}', externalIdIssuer), '{{apiAudience}}', apiAudience), '{{frontendApiClientId}}', frontendApiClientId), '{{backendApiAudience}}', backendApiAudience), '{{mcpGatewayIdentityClientId}}', mcpGatewayIdentityClientId)

var mcpRequireAssetsReadPolicy = '''
<policies>
  <inbound>
    <base />
    <choose>
      <when condition='@{
        var jwt = (Jwt)context.Variables["validatedJwt"];
        if (!jwt.Claims.ContainsKey("scp")) { return true; }
        var scopes = " " + string.Join(" ", jwt.Claims["scp"]) + " ";
        return !scopes.Contains(" assets.read ");
      }'>
        <return-response>
          <set-status code="403" reason="Forbidden" />
          <set-header name="X-Correlation-ID" exists-action="override">
            <value>@((string)context.Variables["correlationId"])</value>
          </set-header>
          <set-header name="X-Authorization-Decision" exists-action="override">
            <value>missing-scope</value>
          </set-header>
        </return-response>
      </when>
      <when condition='@{
        var jwt = (Jwt)context.Variables["validatedJwt"];
        if (!jwt.Claims.ContainsKey("tenant_roles")) { return true; }
        var roles = " " + string.Join(" ", jwt.Claims["tenant_roles"]) + " ";
        return !(roles.Contains(" TenantAdmin ") || roles.Contains(" PortfolioManager ") || roles.Contains(" PortfolioViewer "));
      }'>
        <return-response>
          <set-status code="403" reason="Forbidden" />
          <set-header name="X-Correlation-ID" exists-action="override">
            <value>@((string)context.Variables["correlationId"])</value>
          </set-header>
          <set-header name="X-Authorization-Decision" exists-action="override">
            <value>missing-role</value>
          </set-header>
        </return-response>
      </when>
    </choose>
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>
'''

var mcpRequireAssetsWritePolicy = '''
<policies>
  <inbound>
    <base />
    <choose>
      <when condition='@{
        var jwt = (Jwt)context.Variables["validatedJwt"];
        if (!jwt.Claims.ContainsKey("scp")) { return true; }
        var scopes = " " + string.Join(" ", jwt.Claims["scp"]) + " ";
        return !scopes.Contains(" assets.write ");
      }'>
        <return-response>
          <set-status code="403" reason="Forbidden" />
          <set-header name="X-Correlation-ID" exists-action="override">
            <value>@((string)context.Variables["correlationId"])</value>
          </set-header>
          <set-header name="X-Authorization-Decision" exists-action="override">
            <value>missing-scope</value>
          </set-header>
        </return-response>
      </when>
      <when condition='@{
        var jwt = (Jwt)context.Variables["validatedJwt"];
        if (!jwt.Claims.ContainsKey("tenant_roles")) { return true; }
        var roles = " " + string.Join(" ", jwt.Claims["tenant_roles"]) + " ";
        return !(roles.Contains(" TenantAdmin ") || roles.Contains(" PortfolioManager "));
      }'>
        <return-response>
          <set-status code="403" reason="Forbidden" />
          <set-header name="X-Correlation-ID" exists-action="override">
            <value>@((string)context.Variables["correlationId"])</value>
          </set-header>
          <set-header name="X-Authorization-Decision" exists-action="override">
            <value>missing-role</value>
          </set-header>
        </return-response>
      </when>
    </choose>
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>
'''

var nativeMcpPolicyTemplate = '''
<policies>
  <inbound>
    <base />
    <validate-jwt header-name="Authorization" require-scheme="Bearer" failed-validation-httpcode="401" failed-validation-error-message="Unauthorized">
      <openid-config url="{{externalIdIssuer}}/.well-known/openid-configuration?appid={{frontendApiClientId}}" />
      <audiences>
        <audience>{{apiAudience}}</audience>
      </audiences>
      <issuers>
        <issuer>{{externalIdIssuer}}</issuer>
      </issuers>
      <required-claims>
        <claim name="extension_tenantId" match="all" />
        <claim name="tenant_status" match="all">
          <value>active</value>
        </claim>
      </required-claims>
    </validate-jwt>
    <rate-limit-by-key calls="30" renewal-period="60" counter-key='@(context.Request.Headers.GetValueOrDefault("X-Agent-Id", "foundry-agent"))' />
  </inbound>
  <backend>
    <forward-request />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>
'''

var nativeMcpPolicy = replace(replace(replace(nativeMcpPolicyTemplate, '{{externalIdIssuer}}', externalIdIssuer), '{{apiAudience}}', apiAudience), '{{frontendApiClientId}}', frontendApiClientId)

resource apim 'Microsoft.ApiManagement/service@2023-09-01-preview' = {
  name: 'apim-${take(normalizedEnvironment, 10)}-b2-${take(uniqueString(resourceGroup().id), 8)}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${mcpGatewayIdentityResourceId}': {}
    }
  }
  sku: {
    name: 'BasicV2'
    capacity: 1
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
  }
}

resource frontendApi 'Microsoft.ApiManagement/service/apis@2023-09-01-preview' = {
  parent: apim
  name: 'frontend-api'
  properties: {
    displayName: 'Contoso Asset Management Frontend API'
    path: 'api'
    protocols: [
      'https'
    ]
    serviceUrl: frontendApiUrl
    subscriptionRequired: false
  }
}

resource backendMcpApi 'Microsoft.ApiManagement/service/apis@2023-09-01-preview' = {
  parent: apim
  name: 'backend-mcp-api'
  properties: {
    displayName: 'Contoso Asset Management Backend MCP Tools'
    path: 'mcp/assets'
    protocols: [
      'https'
    ]
    serviceUrl: '${backendApiUrl}/internal'
    subscriptionRequired: false
  }
}

resource backendNativeMcpServer 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = {
  parent: apim
  name: 'backend-assets-mcp'
  properties: {
    type: 'mcp'
    displayName: 'Contoso Asset Management Backend MCP Server'
    description: 'Native APIM MCP server exposing backend asset operations as tools for Foundry hosted agents.'
    path: 'backend-assets-mcp'
    protocols: [
      'https'
    ]
    subscriptionRequired: false
    mcpProperties: {
      transportType: 'streamable'
      endpoints: {
        message: {
          uriTemplate: '/mcp'
        }
      }
    }
    mcpTools: [
      {
        name: 'listPortfolios'
        displayName: 'listPortfolios'
        description: 'List portfolios for the tenant bound to the validated user token.'
        operationId: '/apis/${backendMcpApi.name}/operations/${mcpPortfoliosOperation.name}'
      }
      {
        name: 'getPositionDetail'
        displayName: 'getPositionDetail'
        description: 'Get position detail for a same-tenant portfolio and position.'
        operationId: '/apis/${backendMcpApi.name}/operations/${mcpPositionOperation.name}'
      }
      {
        name: 'approveTransaction'
        displayName: 'approveTransaction'
        description: 'Approve a pending same-tenant transaction. Requires assets.write and a writer role.'
        operationId: '/apis/${backendMcpApi.name}/operations/${mcpApprovalOperation.name}'
      }
    ]
  }
  dependsOn: [
    mcpPortfoliosOperation
    mcpPositionOperation
    mcpApprovalOperation
  ]
}

resource portfoliosOperation 'Microsoft.ApiManagement/service/apis/operations@2023-09-01-preview' = {
  parent: frontendApi
  name: 'get-tenant-portfolios'
  properties: {
    displayName: 'List portfolios'
    method: 'GET'
    urlTemplate: '/tenants/{tenantId}/portfolios'
    templateParameters: [
      {
        name: 'tenantId'
        type: 'string'
        required: true
      }
    ]
    responses: [
      {
        statusCode: 200
      }
    ]
  }
}

resource mcpPortfoliosOperation 'Microsoft.ApiManagement/service/apis/operations@2023-09-01-preview' = {
  parent: backendMcpApi
  name: 'mcp-list-portfolios'
  properties: {
    displayName: 'MCP List portfolios'
    method: 'GET'
    urlTemplate: '/tenants/{tenantId}/portfolios'
    templateParameters: [
      {
        name: 'tenantId'
        type: 'string'
        required: true
      }
    ]
    responses: [
      {
        statusCode: 200
      }
    ]
  }
}

resource positionOperation 'Microsoft.ApiManagement/service/apis/operations@2023-09-01-preview' = {
  parent: frontendApi
  name: 'get-position-detail'
  properties: {
    displayName: 'Get position detail'
    method: 'GET'
    urlTemplate: '/tenants/{tenantId}/portfolios/{portfolioId}/positions/{positionId}'
    templateParameters: [
      {
        name: 'tenantId'
        type: 'string'
        required: true
      }
      {
        name: 'portfolioId'
        type: 'string'
        required: true
      }
      {
        name: 'positionId'
        type: 'string'
        required: true
      }
    ]
    responses: [
      {
        statusCode: 200
      }
    ]
  }
}

resource mcpPositionOperation 'Microsoft.ApiManagement/service/apis/operations@2023-09-01-preview' = {
  parent: backendMcpApi
  name: 'mcp-get-position-detail'
  properties: {
    displayName: 'MCP Get position detail'
    method: 'GET'
    urlTemplate: '/tenants/{tenantId}/portfolios/{portfolioId}/positions/{positionId}'
    templateParameters: [
      {
        name: 'tenantId'
        type: 'string'
        required: true
      }
      {
        name: 'portfolioId'
        type: 'string'
        required: true
      }
      {
        name: 'positionId'
        type: 'string'
        required: true
      }
    ]
    responses: [
      {
        statusCode: 200
      }
    ]
  }
}

resource approvalOperation 'Microsoft.ApiManagement/service/apis/operations@2023-09-01-preview' = {
  parent: frontendApi
  name: 'approve-transaction'
  properties: {
    displayName: 'Approve transaction'
    method: 'POST'
    urlTemplate: '/tenants/{tenantId}/transactions/{transactionId}/approve'
    templateParameters: [
      {
        name: 'tenantId'
        type: 'string'
        required: true
      }
      {
        name: 'transactionId'
        type: 'string'
        required: true
      }
    ]
    responses: [
      {
        statusCode: 200
      }
    ]
  }
}

resource agentChatOperation 'Microsoft.ApiManagement/service/apis/operations@2023-09-01-preview' = {
  parent: frontendApi
  name: 'chat-with-portfolio-agent'
  properties: {
    displayName: 'Chat with portfolio agent'
    method: 'POST'
    urlTemplate: '/tenants/{tenantId}/agent/chat'
    templateParameters: [
      {
        name: 'tenantId'
        type: 'string'
        required: true
      }
    ]
    responses: [
      {
        statusCode: 200
      }
    ]
  }
}

resource mcpApprovalOperation 'Microsoft.ApiManagement/service/apis/operations@2023-09-01-preview' = {
  parent: backendMcpApi
  name: 'mcp-approve-transaction'
  properties: {
    displayName: 'MCP Approve transaction'
    method: 'POST'
    urlTemplate: '/tenants/{tenantId}/transactions/{transactionId}/approve'
    templateParameters: [
      {
        name: 'tenantId'
        type: 'string'
        required: true
      }
      {
        name: 'transactionId'
        type: 'string'
        required: true
      }
    ]
    responses: [
      {
        statusCode: 200
      }
    ]
  }
}

resource policy 'Microsoft.ApiManagement/service/apis/policies@2023-09-01-preview' = {
  parent: frontendApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: commonInboundPolicy
  }
}

resource mcpPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-09-01-preview' = {
  parent: backendMcpApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: mcpInboundPolicy
  }
}

resource nativeMcpPolicyResource 'Microsoft.ApiManagement/service/apis/policies@2025-09-01-preview' = {
  parent: backendNativeMcpServer
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: nativeMcpPolicy
  }
}

resource portfoliosPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2023-09-01-preview' = {
  parent: portfoliosOperation
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: requireAssetsReadPolicy
  }
}

resource mcpPortfoliosPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2023-09-01-preview' = {
  parent: mcpPortfoliosOperation
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: mcpRequireAssetsReadPolicy
  }
}

resource positionPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2023-09-01-preview' = {
  parent: positionOperation
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: requireAssetsReadPolicy
  }
}

resource mcpPositionPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2023-09-01-preview' = {
  parent: mcpPositionOperation
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: mcpRequireAssetsReadPolicy
  }
}

resource approvalPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2023-09-01-preview' = {
  parent: approvalOperation
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: requireAssetsWritePolicy
  }
}

resource agentChatPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2023-09-01-preview' = {
  parent: agentChatOperation
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: requireAssetsReadPolicy
  }
}

resource mcpApprovalPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2023-09-01-preview' = {
  parent: mcpApprovalOperation
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: mcpRequireAssetsWritePolicy
  }
}

resource logAnalyticsDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'send-to-log-analytics'
  scope: apim
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'GatewayLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

output gatewayUrl string = apim.properties.gatewayUrl
