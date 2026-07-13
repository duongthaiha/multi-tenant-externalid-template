namespace Contoso.AssetManagement.Shared;

public sealed record AuthorizationDecision(
    bool Allowed,
    string Decision,
    string? TenantId = null)
{
    public static AuthorizationDecision Allow(string tenantId) =>
        new(true, TenantConstants.AuthorizationDecisions.Allowed, tenantId);

    public static AuthorizationDecision Deny(string decision, string? tenantId = null) =>
        new(false, decision, tenantId);
}
