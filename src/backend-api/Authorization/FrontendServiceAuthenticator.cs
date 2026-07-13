using System.Security.Claims;
using Contoso.AssetManagement.BackendApi.Configuration;
using Contoso.AssetManagement.Shared;
using Microsoft.AspNetCore.Authentication;
using Microsoft.Extensions.Options;

namespace Contoso.AssetManagement.BackendApi.Authorization;

public sealed class FrontendServiceAuthenticator(IOptions<ServiceAuthOptions> options) : IFrontendServiceAuthenticator
{
    private const string RolesClaimType = "roles";
    private readonly ServiceAuthOptions options = options.Value;

    public Task<AuthorizationDecision> AuthenticateReadAsync(HttpContext context) =>
        AuthenticateAsync(context, options.ReadRoles);

    public Task<AuthorizationDecision> AuthenticateWriteAsync(HttpContext context) =>
        AuthenticateAsync(context, options.WriteRoles);

    private async Task<AuthorizationDecision> AuthenticateAsync(HttpContext context, IEnumerable<string> requiredServiceRoles)
    {
        var requiredRoles = requiredServiceRoles.ToArray();
        if (requiredRoles.Length == 0)
        {
            return AuthorizationDecision.Deny(TenantConstants.AuthorizationDecisions.MissingServiceAuthentication);
        }

        var result = await context.AuthenticateAsync(AuthenticationSchemes.ServiceBearer);
        if (!result.Succeeded || result.Principal is null)
        {
            return AuthorizationDecision.Deny(TenantConstants.AuthorizationDecisions.MissingServiceAuthentication);
        }

        var tokenRoles = result.Principal.FindAll(RolesClaimType)
            .Select(claim => claim.Value)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        if (!requiredRoles.Any(tokenRoles.Contains))
        {
            return AuthorizationDecision.Deny(TenantConstants.AuthorizationDecisions.MissingServiceAuthentication);
        }

        return AuthorizationDecision.Allow(context.User.FindFirstValue(TenantConstants.Claims.TenantId) ?? "service-authenticated");
    }
}
