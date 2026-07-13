namespace Contoso.AssetManagement.CustomClaimsProvider.Services;

public sealed record TenantClaimsResolution(
    string TenantId,
    string TenantStatus,
    IReadOnlyCollection<string> Roles);

public sealed record EntitlementResolutionResult(
    bool Succeeded,
    string Decision,
    TenantClaimsResolution? Claims = null)
{
    public static EntitlementResolutionResult Allow(TenantClaimsResolution claims) => new(true, "allowed", claims);
    public static EntitlementResolutionResult Deny(string decision) => new(false, decision);
}

public interface IEntitlementResolver
{
    Task<EntitlementResolutionResult> ResolveAsync(
        string userId,
        string? email,
        string resourceAppId,
        string? selectedTenantId,
        CancellationToken cancellationToken);
}
