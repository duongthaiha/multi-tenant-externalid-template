using Contoso.AssetManagement.Shared;

namespace Contoso.AssetManagement.BackendApi.Authorization;

public interface IFrontendServiceAuthenticator
{
    Task<AuthorizationDecision> AuthenticateReadAsync(HttpContext context);

    Task<AuthorizationDecision> AuthenticateWriteAsync(HttpContext context);
}
