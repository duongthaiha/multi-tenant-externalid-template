using Microsoft.Azure.Cosmos;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Contoso.AssetManagement.Shared;

namespace Contoso.AssetManagement.CustomClaimsProvider.Services;

public sealed class CosmosEntitlementResolver : IEntitlementResolver
{
    private static readonly HashSet<string> AllowedRoles = new(StringComparer.Ordinal)
    {
        TenantConstants.Roles.TenantAdmin,
        TenantConstants.Roles.PortfolioManager,
        TenantConstants.Roles.PortfolioViewer
    };

    private readonly Container _tenants;
    private readonly Container _memberships;
    private readonly Container _roleAssignments;
    private readonly ILogger<CosmosEntitlementResolver> _logger;

    public CosmosEntitlementResolver(
        CosmosClient cosmosClient,
        IOptions<ControlPlaneCosmosOptions> options,
        ILogger<CosmosEntitlementResolver> logger)
    {
        var value = options.Value;
        var database = cosmosClient.GetDatabase(value.DatabaseName);
        _tenants = database.GetContainer(value.TenantsContainerName);
        _memberships = database.GetContainer(value.MembershipsContainerName);
        _roleAssignments = database.GetContainer(value.RoleAssignmentsContainerName);
        _logger = logger;
    }

    public async Task<EntitlementResolutionResult> ResolveAsync(
        string userId,
        string? email,
        string resourceAppId,
        string? selectedTenantId,
        CancellationToken cancellationToken)
    {
        var memberships = await GetActiveMembershipsAsync(userId, cancellationToken);
        if (memberships.Count == 0 && !string.IsNullOrWhiteSpace(email))
        {
            memberships = await GetActiveMembershipsByEmailAsync(email, cancellationToken);
        }

        if (memberships.Count == 0)
        {
            return EntitlementResolutionResult.Deny("missing-active-membership");
        }

        var selectedMembership = SelectMembership(memberships, selectedTenantId);
        if (selectedMembership is null)
        {
            return EntitlementResolutionResult.Deny(!string.IsNullOrWhiteSpace(selectedTenantId)
                ? "selected-tenant-not-authorized"
                : "multiple-active-tenants-without-selection");
        }

        var tenant = await GetTenantAsync(selectedMembership.TenantId, cancellationToken);
        if (tenant is null)
        {
            return EntitlementResolutionResult.Deny("tenant-not-found");
        }

        if (!string.Equals(tenant.Status, TenantConstants.TenantStatus.Active, StringComparison.OrdinalIgnoreCase))
        {
            return EntitlementResolutionResult.Deny(TenantConstants.AuthorizationDecisions.TenantInactive);
        }

        var roles = await GetRolesAsync(selectedMembership.UserId, tenant.TenantId, resourceAppId, cancellationToken);
        if (roles.Count == 0)
        {
            return EntitlementResolutionResult.Deny("unresolved-roles");
        }

        _logger.LogDebug("Resolved claims from control plane for user {UserId}, tenant {TenantId}, roleCount {RoleCount}", userId, tenant.TenantId, roles.Count);
        return EntitlementResolutionResult.Allow(new TenantClaimsResolution(tenant.TenantId, tenant.Status, roles));
    }

    private async Task<IReadOnlyList<MembershipDocument>> GetActiveMembershipsAsync(string userId, CancellationToken cancellationToken)
    {
        const string queryText = "SELECT * FROM c WHERE c.userId = @userId AND LOWER(c.status) = @status";
        var query = new QueryDefinition(queryText)
            .WithParameter("@userId", userId)
            .WithParameter("@status", TenantConstants.TenantStatus.Active);

        return await ReadAllAsync<MembershipDocument>(_memberships, query, new QueryRequestOptions
        {
            PartitionKey = new PartitionKey(userId),
            MaxItemCount = 10
        }, cancellationToken);
    }


    private async Task<IReadOnlyList<MembershipDocument>> GetActiveMembershipsByEmailAsync(string email, CancellationToken cancellationToken)
    {
        const string queryText = "SELECT * FROM c WHERE LOWER(c.email) = @email AND LOWER(c.status) = @status";
        var query = new QueryDefinition(queryText)
            .WithParameter("@email", email.ToLowerInvariant())
            .WithParameter("@status", TenantConstants.TenantStatus.Active);

        return await ReadAllAsync<MembershipDocument>(_memberships, query, new QueryRequestOptions
        {
            MaxItemCount = 10
        }, cancellationToken);
    }

    private static MembershipDocument? SelectMembership(IReadOnlyList<MembershipDocument> memberships, string? selectedTenantId)
    {
        if (!string.IsNullOrWhiteSpace(selectedTenantId))
        {
            return memberships.SingleOrDefault(m => string.Equals(m.TenantId, selectedTenantId, StringComparison.Ordinal));
        }

        return memberships.Count == 1 ? memberships[0] : null;
    }

    private async Task<TenantDocument?> GetTenantAsync(string tenantId, CancellationToken cancellationToken)
    {
        try
        {
            var response = await _tenants.ReadItemAsync<TenantDocument>($"tenant-{tenantId}", new PartitionKey(tenantId), cancellationToken: cancellationToken);
            return response.Resource;
        }
        catch (CosmosException ex) when (ex.StatusCode == System.Net.HttpStatusCode.NotFound)
        {
            return null;
        }
    }

    private async Task<IReadOnlyCollection<string>> GetRolesAsync(
        string userId,
        string tenantId,
        string resourceAppId,
        CancellationToken cancellationToken)
    {
        var resourceAppUri = Guid.TryParse(resourceAppId, out _)
            ? $"api://{resourceAppId}"
            : resourceAppId;
        const string queryText = "SELECT * FROM c WHERE c.tenantId = @tenantId AND c.userId = @userId AND (c.resourceAppId = @resourceAppId OR c.resourceAppId = @resourceAppUri)";
        var query = new QueryDefinition(queryText)
            .WithParameter("@tenantId", tenantId)
            .WithParameter("@userId", userId)
            .WithParameter("@resourceAppId", resourceAppId)
            .WithParameter("@resourceAppUri", resourceAppUri);

        var assignments = await ReadAllAsync<RoleAssignmentDocument>(_roleAssignments, query, new QueryRequestOptions
        {
            PartitionKey = new PartitionKey(tenantId),
            MaxItemCount = 5
        }, cancellationToken);

        return assignments
            .SelectMany(a => a.Roles ?? [])
            .Where(role => AllowedRoles.Contains(role))
            .Distinct(StringComparer.Ordinal)
            .OrderBy(role => role, StringComparer.Ordinal)
            .ToArray();
    }

    private static async Task<IReadOnlyList<T>> ReadAllAsync<T>(
        Container container,
        QueryDefinition query,
        QueryRequestOptions requestOptions,
        CancellationToken cancellationToken)
    {
        var results = new List<T>();
        using var iterator = container.GetItemQueryIterator<T>(query, requestOptions: requestOptions);
        while (iterator.HasMoreResults)
        {
            foreach (var item in await iterator.ReadNextAsync(cancellationToken))
            {
                results.Add(item);
            }
        }

        return results;
    }

    private sealed class MembershipDocument
    {
        public string Id { get; set; } = string.Empty;
        public string UserId { get; set; } = string.Empty;
        public string Email { get; set; } = string.Empty;
        public string TenantId { get; set; } = string.Empty;
        public string Status { get; set; } = string.Empty;
    }

    private sealed class TenantDocument
    {
        public string Id { get; set; } = string.Empty;
        public string TenantId { get; set; } = string.Empty;
        public string Status { get; set; } = string.Empty;
    }

    private sealed class RoleAssignmentDocument
    {
        public string Id { get; set; } = string.Empty;
        public string UserId { get; set; } = string.Empty;
        public string TenantId { get; set; } = string.Empty;
        public string ResourceAppId { get; set; } = string.Empty;
        public IReadOnlyCollection<string>? Roles { get; set; }
    }
}
