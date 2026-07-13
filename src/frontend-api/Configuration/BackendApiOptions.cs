using Contoso.AssetManagement.Shared;

namespace Contoso.AssetManagement.FrontendApi.Configuration;

public sealed class BackendApiOptions
{
    public Uri BaseAddress { get; init; } = new("http://localhost:8080");
    public string ServiceAuthorizationHeaderName { get; init; } = TenantConstants.Headers.ServiceAuthorization;
    public string[] ServiceTokenScopes { get; init; } = [];
    public TimeSpan Timeout { get; init; } = TimeSpan.FromSeconds(10);
}
