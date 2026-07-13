using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;

namespace Contoso.AssetManagement.Shared.Auth;

public sealed record DelegatedUserIdentity(string AppUserId, string FoundryUserIdentity);

public static class DelegatedUserIdentityFactory
{
    private const int MaxFoundryIdentityLength = 256;
    private const int MaxTenantSegmentLength = 128;

    public static DelegatedUserIdentity FromValidatedClaims(ClaimsPrincipal principal, string validatedTenantId)
    {
        ArgumentNullException.ThrowIfNull(principal);

        if (string.IsNullOrWhiteSpace(validatedTenantId))
        {
            throw new ArgumentException("Validated tenant id is required.", nameof(validatedTenantId));
        }

        var issuer = GetIssuer(principal);
        var subject = principal.GetUserId();
        if (string.IsNullOrWhiteSpace(issuer) || string.IsNullOrWhiteSpace(subject))
        {
            throw new InvalidOperationException("Validated issuer and oid or sub claims are required.");
        }

        var userHash = Sha256Hex($"{issuer}|{subject}");
        var appUserId = $"user-{userHash}";
        var foundryUserIdentity = $"tenant-{NormalizeTenantSegment(validatedTenantId)}-user-{userHash}";

        if (!IsFoundryUserIdentity(foundryUserIdentity))
        {
            throw new InvalidOperationException("Derived Foundry user identity is invalid.");
        }

        return new DelegatedUserIdentity(appUserId, foundryUserIdentity);
    }

    public static bool IsFoundryUserIdentity(string value) =>
        value.Length is >= 1 and <= MaxFoundryIdentityLength
        && value.All(IsFoundrySafeCharacter);

    private static string? GetIssuer(ClaimsPrincipal principal)
    {
        var issuerClaim = principal.FindFirst(TenantConstants.Claims.Issuer);
        if (!string.IsNullOrWhiteSpace(issuerClaim?.Value))
        {
            return issuerClaim.Value;
        }

        return principal.Claims
            .Select(claim => claim.Issuer)
            .FirstOrDefault(issuer => !string.IsNullOrWhiteSpace(issuer) && issuer != ClaimsIdentity.DefaultIssuer);
    }

    private static string NormalizeTenantSegment(string tenantId)
    {
        var normalized = new string(tenantId
            .Trim()
            .ToLowerInvariant()
            .Select(character => IsFoundrySafeCharacter(character) ? character : '-')
            .ToArray())
            .Trim('-', '.', '_', ':', '@');

        if (normalized.Length == 0)
        {
            return Sha256Hex(tenantId)[..32];
        }

        if (normalized.Length <= MaxTenantSegmentLength)
        {
            return normalized;
        }

        return $"{normalized[..111]}-{Sha256Hex(tenantId)[..16]}";
    }

    private static bool IsFoundrySafeCharacter(char character) =>
        char.IsAsciiLetterOrDigit(character)
        || character is '.' or '_' or ':' or '-' or '@';

    private static string Sha256Hex(string value) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();
}
