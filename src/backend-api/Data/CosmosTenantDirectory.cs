using System.Net;
using Azure.Core;
using Contoso.AssetManagement.BackendApi.Configuration;
using Contoso.AssetManagement.BackendApi.Infrastructure;
using Contoso.AssetManagement.Shared;
using Microsoft.Azure.Cosmos;
using Microsoft.Extensions.Options;

namespace Contoso.AssetManagement.BackendApi.Data;

public sealed class CosmosTenantDirectory : ITenantDirectory
{
    private readonly Container tenants;

    public CosmosTenantDirectory(
        TokenCredential credential,
        IOptions<ControlPlaneOptions> options,
        SystemTextJsonCosmosSerializer serializer)
    {
        var settings = options.Value;
        if (string.IsNullOrWhiteSpace(settings.Endpoint))
        {
            throw new InvalidOperationException("ControlPlane:Endpoint is required for TenantDirectory routing.");
        }

        var client = new CosmosClient(settings.Endpoint, credential, new CosmosClientOptions { Serializer = serializer });
        tenants = client.GetContainer(settings.DatabaseName, settings.TenantsContainerName);
    }

    public async Task<TenantDirectoryEntry?> GetTenantAsync(string validatedTokenTenantId, CancellationToken cancellationToken)
    {
        try
        {
            var response = await tenants.ReadItemAsync<TenantDirectoryEntry>(
                id: $"tenant-{validatedTokenTenantId}",
                partitionKey: new PartitionKey(validatedTokenTenantId),
                cancellationToken: cancellationToken);

            return response.Resource;
        }
        catch (CosmosException exception) when (exception.StatusCode == HttpStatusCode.NotFound)
        {
            return null;
        }
    }
}
