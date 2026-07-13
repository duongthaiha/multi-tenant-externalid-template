using System.Security.Claims;

namespace Contoso.AssetManagement.Shared.Auth;

public static class TenantAuthorization
{
    public static AuthorizationDecision AuthorizeRead(ClaimsPrincipal principal, string routeTenantId) =>
        Authorize(principal, routeTenantId, TenantConstants.Scopes.AssetsRead, TenantConstants.Roles.AssetReaders);

    public static AuthorizationDecision AuthorizeApproval(ClaimsPrincipal principal, string routeTenantId) =>
        Authorize(principal, routeTenantId, TenantConstants.Scopes.AssetsWrite, TenantConstants.Roles.AssetWriters);

    public static AuthorizationDecision AuthorizeTenantBinding(ClaimsPrincipal principal, string routeTenantId)
    {
        var tokenTenantId = principal.GetTenantId();
        if (string.IsNullOrWhiteSpace(tokenTenantId))
        {
            return AuthorizationDecision.Deny(TenantConstants.AuthorizationDecisions.MissingTenantClaim);
        }

        if (!principal.HasActiveTenant())
        {
            return AuthorizationDecision.Deny(TenantConstants.AuthorizationDecisions.TenantInactive, tokenTenantId);
        }

        if (!string.Equals(tokenTenantId, routeTenantId, StringComparison.Ordinal))
        {
            return AuthorizationDecision.Deny(TenantConstants.AuthorizationDecisions.TenantMismatch, tokenTenantId);
        }

        return AuthorizationDecision.Allow(tokenTenantId);
    }

    private static AuthorizationDecision Authorize(
        ClaimsPrincipal principal,
        string routeTenantId,
        string requiredScope,
        IEnumerable<string> requiredRoles)
    {
        var tenantDecision = AuthorizeTenantBinding(principal, routeTenantId);
        if (!tenantDecision.Allowed)
        {
            return tenantDecision;
        }

        if (!principal.HasScope(requiredScope))
        {
            return AuthorizationDecision.Deny(TenantConstants.AuthorizationDecisions.MissingScope, tenantDecision.TenantId);
        }

        if (!principal.HasAnyTenantRole(requiredRoles))
        {
            return AuthorizationDecision.Deny(TenantConstants.AuthorizationDecisions.MissingRole, tenantDecision.TenantId);
        }

        return tenantDecision;
    }
}
