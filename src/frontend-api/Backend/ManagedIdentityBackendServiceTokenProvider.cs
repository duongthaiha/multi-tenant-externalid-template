using Azure.Core;
using Contoso.AssetManagement.FrontendApi.Configuration;
using Microsoft.Extensions.Options;

namespace Contoso.AssetManagement.FrontendApi.Backend;

public sealed class ManagedIdentityBackendServiceTokenProvider(
    TokenCredential credential,
    IOptions<BackendApiOptions> options) : IBackendServiceTokenProvider
{
    private readonly BackendApiOptions options = options.Value;

    public async Task<string> GetServiceTokenAsync(CancellationToken cancellationToken)
    {
        if (options.ServiceTokenScopes.Length == 0)
        {
            throw new InvalidOperationException("BackendApi__ServiceTokenScopes must contain the backend API /.default scope for service authentication.");
        }

        var token = await credential.GetTokenAsync(new TokenRequestContext(options.ServiceTokenScopes), cancellationToken);
        return token.Token;
    }
}
