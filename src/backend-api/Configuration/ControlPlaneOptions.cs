namespace Contoso.AssetManagement.BackendApi.Configuration;

public sealed class ControlPlaneOptions
{
    public string Endpoint { get; init; } = string.Empty;
    public string DatabaseName { get; init; } = "tenant-directory";
    public string TenantsContainerName { get; init; } = "tenants";
}
