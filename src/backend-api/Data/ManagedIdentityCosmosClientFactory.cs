using System.Collections.Concurrent;
using Azure.Core;
using Azure.Identity;
using Contoso.AssetManagement.BackendApi.Infrastructure;
using Contoso.AssetManagement.Shared;
using Microsoft.Azure.Cosmos;

namespace Contoso.AssetManagement.BackendApi.Data;

public sealed class ManagedIdentityCosmosClientFactory(
    TokenCredential credential,
    SystemTextJsonCosmosSerializer serializer) : ICosmosClientFactory
{
    private readonly ConcurrentDictionary<string, CosmosClient> clients = new(StringComparer.OrdinalIgnoreCase);

    public Container GetContainer(TenantDirectoryEntry tenant, string containerName)
    {
        if (string.IsNullOrWhiteSpace(tenant.CosmosIdentityClientId))
        {
            throw new InvalidOperationException($"Tenant {tenant.TenantId} is missing CosmosIdentityClientId.");
        }

        var cacheKey = $"{tenant.CosmosAccountEndpoint}|{tenant.CosmosIdentityClientId}";
        var client = clients.GetOrAdd(cacheKey, _ =>
            new CosmosClient(
                tenant.CosmosAccountEndpoint,
                new ManagedIdentityCredential(tenant.CosmosIdentityClientId),
                new CosmosClientOptions { Serializer = serializer }));

        return client.GetContainer(tenant.DatabaseName, containerName);
    }
}
