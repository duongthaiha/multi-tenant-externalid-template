using Contoso.AssetManagement.Shared;

namespace Contoso.AssetManagement.BackendApi.Data;

public interface ITenantDirectory
{
    Task<TenantDirectoryEntry?> GetTenantAsync(string validatedTokenTenantId, CancellationToken cancellationToken);
}
