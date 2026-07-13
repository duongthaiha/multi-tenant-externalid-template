namespace Contoso.AssetManagement.CustomClaimsProvider.Services;

public sealed class ControlPlaneCosmosOptions
{
    public const string SectionName = "ControlPlaneCosmos";

    public string Endpoint { get; init; } = string.Empty;
    public string DatabaseName { get; init; } = "tenant-directory";
    public string TenantsContainerName { get; init; } = "tenants";
    public string MembershipsContainerName { get; init; } = "memberships";
    public string RoleAssignmentsContainerName { get; init; } = "roleAssignments";
    public int RequestTimeoutMilliseconds { get; init; } = 1800;
}
