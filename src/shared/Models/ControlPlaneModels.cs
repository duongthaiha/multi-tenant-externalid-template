namespace Contoso.AssetManagement.Shared;

public sealed record TenantDirectoryEntry(
    string Id,
    string TenantId,
    string DisplayName,
    string Status,
    string Region,
    string CosmosAccountEndpoint,
    string DatabaseName,
    string ContainerName,
    string? CosmosIdentityClientId = null,
    string? CosmosIdentityPrincipalId = null);

public sealed record UserTenantMembership(
    string Id,
    string UserId,
    string Email,
    string TenantId,
    string Status);

public sealed record RoleAssignment(
    string Id,
    string UserId,
    string TenantId,
    IReadOnlyCollection<string> Roles,
    string ResourceAppId);

public sealed record TenantOnboardingState(
    string Id,
    string TenantId,
    string ProvisioningStatus,
    DateTimeOffset CreatedAt,
    DateTimeOffset UpdatedAt);
