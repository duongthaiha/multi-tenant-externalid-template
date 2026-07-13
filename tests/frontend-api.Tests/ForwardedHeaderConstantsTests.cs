using Contoso.AssetManagement.Shared;

namespace Contoso.AssetManagement.FrontendApi.Tests;

public sealed class ForwardedHeaderConstantsTests
{
    [Fact]
    public void ForwardedHeadersUseHostedAgentHeaderNames()
    {
        Assert.Equal("X-Authenticated-Tenant", TenantConstants.Headers.AuthenticatedTenant);
        Assert.Equal("X-Authenticated-User", TenantConstants.Headers.AuthenticatedUser);
        Assert.Equal("X-User-Authorization", TenantConstants.Headers.UserAuthorization);
        Assert.Equal("X-Service-Authorization", TenantConstants.Headers.ServiceAuthorization);
        Assert.Equal("X-Correlation-ID", TenantConstants.Headers.CorrelationId);
        Assert.Equal("X-Agent-Id", TenantConstants.Headers.AgentId);
        Assert.Equal("X-Authorization-Decision", TenantConstants.Headers.AuthorizationDecision);
        Assert.Equal("x-client-X-Correlation-ID", TenantConstants.Headers.ClientForwarded(TenantConstants.Headers.CorrelationId));
        Assert.Equal("x-ms-user-identity", TenantConstants.Headers.FoundryUserIdentity);
    }
}
