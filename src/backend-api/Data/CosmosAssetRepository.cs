using System.Net;
using Contoso.AssetManagement.BackendApi.Configuration;
using Contoso.AssetManagement.Shared;
using Microsoft.Azure.Cosmos;
using Microsoft.Extensions.Options;

namespace Contoso.AssetManagement.BackendApi.Data;

public sealed class CosmosAssetRepository(
    ICosmosClientFactory cosmosClientFactory,
    IOptions<AssetDataOptions> options) : IAssetRepository
{
    private readonly AssetDataOptions options = options.Value;

    public async Task<IReadOnlyList<Portfolio>> ListPortfoliosAsync(TenantDirectoryEntry tenant, CancellationToken cancellationToken)
    {
        var containerName = string.IsNullOrWhiteSpace(tenant.ContainerName) ? options.PortfolioContainerName : tenant.ContainerName;
        var container = cosmosClientFactory.GetContainer(tenant, containerName);
        var query = new QueryDefinition(
            "SELECT * FROM c WHERE c.tenantId = @tenantId AND c.documentType = @documentType ORDER BY c.name")
            .WithParameter("@tenantId", tenant.TenantId)
            .WithParameter("@documentType", "Portfolio");

        return await ReadAllAsync<Portfolio>(container, query, tenant.TenantId, cancellationToken);
    }

    public async Task<Position?> GetPositionAsync(
        TenantDirectoryEntry tenant,
        string portfolioId,
        string positionId,
        CancellationToken cancellationToken)
    {
        var container = cosmosClientFactory.GetContainer(tenant, options.PositionsContainerName);
        var query = new QueryDefinition(
            "SELECT * FROM c WHERE c.tenantId = @tenantId AND c.portfolioId = @portfolioId AND c.id = @positionId AND c.documentType = @documentType")
            .WithParameter("@tenantId", tenant.TenantId)
            .WithParameter("@portfolioId", portfolioId)
            .WithParameter("@positionId", positionId)
            .WithParameter("@documentType", "Position");

        return await ReadSingleAsync<Position>(container, query, tenant.TenantId, cancellationToken);
    }

    public async Task<TransactionApproval?> ApproveTransactionAsync(
        TenantDirectoryEntry tenant,
        string transactionId,
        string approvedBy,
        CancellationToken cancellationToken)
    {
        var container = cosmosClientFactory.GetContainer(tenant, options.TransactionApprovalsContainerName);
        TransactionApproval approval;
        try
        {
            var response = await container.ReadItemAsync<TransactionApproval>(
                transactionId,
                new PartitionKey(tenant.TenantId),
                cancellationToken: cancellationToken);
            approval = response.Resource;
        }
        catch (CosmosException exception) when (exception.StatusCode == HttpStatusCode.NotFound)
        {
            return null;
        }

        if (!string.Equals(approval.TenantId, tenant.TenantId, StringComparison.Ordinal) ||
            !string.Equals(approval.Status, "Pending", StringComparison.OrdinalIgnoreCase))
        {
            return approval;
        }

        var updated = approval with
        {
            Status = "Approved",
            ApprovedBy = approvedBy,
            ApprovedAt = DateTimeOffset.UtcNow
        };

        var replaceResponse = await container.ReplaceItemAsync(
            updated,
            updated.Id,
            new PartitionKey(tenant.TenantId),
            cancellationToken: cancellationToken);

        return replaceResponse.Resource;
    }

    private static async Task<IReadOnlyList<T>> ReadAllAsync<T>(
        Container container,
        QueryDefinition query,
        string tenantId,
        CancellationToken cancellationToken)
    {
        var results = new List<T>();
        using var iterator = container.GetItemQueryIterator<T>(
            query,
            requestOptions: new QueryRequestOptions { PartitionKey = new PartitionKey(tenantId) });

        while (iterator.HasMoreResults)
        {
            var page = await iterator.ReadNextAsync(cancellationToken);
            results.AddRange(page);
        }

        return results;
    }

    private static async Task<T?> ReadSingleAsync<T>(
        Container container,
        QueryDefinition query,
        string tenantId,
        CancellationToken cancellationToken)
    {
        using var iterator = container.GetItemQueryIterator<T>(
            query,
            requestOptions: new QueryRequestOptions
            {
                PartitionKey = new PartitionKey(tenantId),
                MaxItemCount = 1
            });

        while (iterator.HasMoreResults)
        {
            var page = await iterator.ReadNextAsync(cancellationToken);
            var item = page.FirstOrDefault();
            if (item is not null)
            {
                return item;
            }
        }

        return default;
    }
}
