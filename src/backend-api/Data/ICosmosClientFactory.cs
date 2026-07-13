using Contoso.AssetManagement.Shared;
using Microsoft.Azure.Cosmos;

namespace Contoso.AssetManagement.BackendApi.Data;

public interface ICosmosClientFactory
{
    Container GetContainer(TenantDirectoryEntry tenant, string containerName);
}
