using System.Security.Claims;

namespace Contoso.AssetManagement.Shared.Auth;

public static class ClaimsPrincipalExtensions
{
    public static string? GetTenantId(this ClaimsPrincipal principal) =>
        principal.FindFirst(TenantConstants.Claims.TenantId)?.Value;

    public static string? GetTenantStatus(this ClaimsPrincipal principal) =>
        principal.FindFirst(TenantConstants.Claims.TenantStatus)?.Value;

    public static string? GetUserId(this ClaimsPrincipal principal) =>
        principal.FindFirst(TenantConstants.Claims.ObjectId)?.Value
        ?? principal.FindFirst(TenantConstants.Claims.Subject)?.Value;

    public static string? GetPreferredUsername(this ClaimsPrincipal principal) =>
        principal.FindFirst(TenantConstants.Claims.PreferredUsername)?.Value;

    public static IReadOnlySet<string> GetScopes(this ClaimsPrincipal principal)
    {
        var scopes = principal.FindAll(TenantConstants.Claims.Scope)
            .SelectMany(claim => claim.Value.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries));

        return scopes.ToHashSet(StringComparer.OrdinalIgnoreCase);
    }

    public static IReadOnlySet<string> GetTenantRoles(this ClaimsPrincipal principal)
    {
        var roles = principal.FindAll(TenantConstants.Claims.Roles)
            .Concat(principal.FindAll(ClaimTypes.Role))
            .SelectMany(claim => claim.Value.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries));

        return roles.ToHashSet(StringComparer.OrdinalIgnoreCase);
    }

    public static bool HasScope(this ClaimsPrincipal principal, string scope) =>
        principal.GetScopes().Contains(scope);

    public static bool HasAnyTenantRole(this ClaimsPrincipal principal, IEnumerable<string> roles)
    {
        var actualRoles = principal.GetTenantRoles();
        return roles.Any(actualRoles.Contains);
    }

    public static bool HasActiveTenant(this ClaimsPrincipal principal) =>
        string.Equals(principal.GetTenantStatus(), TenantConstants.TenantStatus.Active, StringComparison.OrdinalIgnoreCase);
}
