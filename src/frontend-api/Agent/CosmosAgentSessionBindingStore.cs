using System.Net;
using System.Security.Cryptography;
using System.Text;
using Azure.Core;
using Contoso.AssetManagement.FrontendApi.Configuration;
using Microsoft.Azure.Cosmos;
using Microsoft.Extensions.Options;

namespace Contoso.AssetManagement.FrontendApi.Agent;

public sealed class CosmosAgentSessionBindingStore : IAgentSessionBindingStore, IDisposable
{
    private readonly CosmosClient client;
    private readonly Container container;

    public CosmosAgentSessionBindingStore(
        IOptions<AgentSessionBindingStoreOptions> options,
        TokenCredential credential)
    {
        var storeOptions = options.Value;
        if (storeOptions.Endpoint is null)
        {
            throw new InvalidOperationException("AgentSessionBindingStore:Endpoint is required when UseInMemory is false.");
        }

        client = new CosmosClient(storeOptions.Endpoint.ToString(), credential, new CosmosClientOptions
        {
            SerializerOptions = new CosmosSerializationOptions
            {
                PropertyNamingPolicy = CosmosPropertyNamingPolicy.CamelCase
            }
        });
        container = client.GetContainer(storeOptions.DatabaseName, storeOptions.ContainerName);
    }

    public async Task<AgentSessionBinding> CreateAsync(AgentSessionBinding binding, CancellationToken cancellationToken)
    {
        var document = BindingDocument.FromBinding(binding);
        var response = await container.CreateItemAsync(document, new PartitionKey(document.OwnerPartitionKey), cancellationToken: cancellationToken);
        return response.Resource.ToBinding(response.ETag);
    }

    public async Task<AgentSessionBinding?> GetOwnedAsync(
        AgentSessionHandle sessionHandle,
        string tenantId,
        string userId,
        string agentName,
        CancellationToken cancellationToken)
    {
        var partitionKey = ComputeOwnerPartitionKey(tenantId, userId);
        try
        {
            var response = await container.ReadItemAsync<BindingDocument>(
                sessionHandle.Value,
                new PartitionKey(partitionKey),
                cancellationToken: cancellationToken);

            var document = response.Resource;
            if (!string.Equals(document.AgentName, agentName, StringComparison.Ordinal)
                || !string.Equals(document.TenantId, tenantId, StringComparison.Ordinal)
                || !string.Equals(document.UserId, userId, StringComparison.Ordinal))
            {
                return null;
            }

            return document.ToBinding(response.ETag);
        }
        catch (CosmosException exception) when (exception.StatusCode == HttpStatusCode.NotFound)
        {
            return null;
        }
    }

    public Task<AgentSessionBinding> MarkUsedAsync(
        AgentSessionBinding binding,
        DateTimeOffset lastUsedAt,
        DateTimeOffset expiresAt,
        string correlationId,
        CancellationToken cancellationToken) =>
        ReplaceAsync(binding with
        {
            LastUsedAt = lastUsedAt,
            ExpiresAt = expiresAt,
            Status = AgentSessionStatus.Active,
            CorrelationId = correlationId
        }, cancellationToken);

    public Task<AgentSessionBinding> MarkStoppedAsync(
        AgentSessionBinding binding,
        DateTimeOffset stoppedAt,
        string correlationId,
        CancellationToken cancellationToken) =>
        ReplaceAsync(binding with
        {
            LastUsedAt = stoppedAt,
            Status = AgentSessionStatus.Stopped,
            CorrelationId = correlationId
        }, cancellationToken);

    public Task<AgentSessionBinding> MarkDeletedAsync(
        AgentSessionBinding binding,
        DateTimeOffset deletedAt,
        string correlationId,
        CancellationToken cancellationToken) =>
        ReplaceAsync(binding with
        {
            LastUsedAt = deletedAt,
            Status = AgentSessionStatus.Deleted,
            CorrelationId = correlationId
        }, cancellationToken);

    public Task<AgentSessionBinding> MarkExpiredAsync(
        AgentSessionBinding binding,
        DateTimeOffset expiredAt,
        string correlationId,
        CancellationToken cancellationToken) =>
        ReplaceAsync(binding with
        {
            LastUsedAt = expiredAt,
            ExpiresAt = expiredAt,
            Status = AgentSessionStatus.Expired,
            CorrelationId = correlationId
        }, cancellationToken);

    public Task<AgentSessionBinding> MarkReplacedAsync(
        AgentSessionBinding binding,
        AgentSessionHandle replacementSessionHandle,
        DateTimeOffset replacedAt,
        string correlationId,
        CancellationToken cancellationToken) =>
        ReplaceAsync(binding with
        {
            LastUsedAt = replacedAt,
            Status = AgentSessionStatus.Replaced,
            ReplacedBySessionHandle = replacementSessionHandle,
            CorrelationId = correlationId
        }, cancellationToken);

    public async Task<int> DeleteExpiredAsync(DateTimeOffset expiresBefore, CancellationToken cancellationToken)
    {
        var query = new QueryDefinition("SELECT c.id, c.ownerPartitionKey FROM c WHERE c.expiresAt < @expiresBefore")
            .WithParameter("@expiresBefore", expiresBefore);
        using var iterator = container.GetItemQueryIterator<ExpiredBindingDocument>(query);
        var deleted = 0;

        while (iterator.HasMoreResults)
        {
            foreach (var document in await iterator.ReadNextAsync(cancellationToken))
            {
                try
                {
                    await container.DeleteItemAsync<BindingDocument>(
                        document.Id,
                        new PartitionKey(document.OwnerPartitionKey),
                        cancellationToken: cancellationToken);
                    deleted++;
                }
                catch (CosmosException exception) when (exception.StatusCode == HttpStatusCode.NotFound)
                {
                }
            }
        }

        return deleted;
    }

    public void Dispose() => client.Dispose();

    private async Task<AgentSessionBinding> ReplaceAsync(AgentSessionBinding binding, CancellationToken cancellationToken)
    {
        var document = BindingDocument.FromBinding(binding);
        var requestOptions = string.IsNullOrWhiteSpace(binding.ETag)
            ? null
            : new ItemRequestOptions { IfMatchEtag = binding.ETag };

        var response = await container.ReplaceItemAsync(
            document,
            document.Id,
            new PartitionKey(document.OwnerPartitionKey),
            requestOptions,
            cancellationToken);
        return response.Resource.ToBinding(response.ETag);
    }

    private static string ComputeOwnerPartitionKey(string tenantId, string userId)
    {
        var normalized = $"{tenantId.Trim().ToLowerInvariant()}|{userId.Trim().ToLowerInvariant()}";
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(normalized))).ToLowerInvariant();
    }

    private sealed class ExpiredBindingDocument
    {
        public string Id { get; set; } = string.Empty;
        public string OwnerPartitionKey { get; set; } = string.Empty;
    }

    private sealed class BindingDocument
    {
        public string Id { get; set; } = string.Empty;
        public string OwnerPartitionKey { get; set; } = string.Empty;
        public string FoundryAgentSessionId { get; set; } = string.Empty;
        public string TenantId { get; set; } = string.Empty;
        public string UserId { get; set; } = string.Empty;
        public string FoundryUserIdentity { get; set; } = string.Empty;
        public string AgentName { get; set; } = string.Empty;
        public string ProtocolMode { get; set; } = string.Empty;
        public string? FoundryConversationId { get; set; }
        public DateTimeOffset CreatedAt { get; set; }
        public DateTimeOffset LastUsedAt { get; set; }
        public DateTimeOffset ExpiresAt { get; set; }
        public string Status { get; set; } = string.Empty;
        public string? CorrelationId { get; set; }
        public string? ReplacedBySessionHandle { get; set; }
        public int Ttl { get; set; }

        public static BindingDocument FromBinding(AgentSessionBinding binding)
        {
            var ttl = (int)Math.Ceiling((binding.ExpiresAt - DateTimeOffset.UtcNow).TotalSeconds);
            return new BindingDocument
            {
                Id = binding.SessionHandle.Value,
                OwnerPartitionKey = ComputeOwnerPartitionKey(binding.TenantId, binding.UserId),
                FoundryAgentSessionId = binding.FoundryAgentSessionId.Value,
                TenantId = binding.TenantId,
                UserId = binding.UserId,
                FoundryUserIdentity = binding.FoundryUserIdentity.Value,
                AgentName = binding.AgentName,
                ProtocolMode = binding.ProtocolMode.ToString(),
                FoundryConversationId = binding.FoundryConversationId?.Value,
                CreatedAt = binding.CreatedAt,
                LastUsedAt = binding.LastUsedAt,
                ExpiresAt = binding.ExpiresAt,
                Status = binding.Status.ToString(),
                CorrelationId = binding.CorrelationId,
                ReplacedBySessionHandle = binding.ReplacedBySessionHandle?.Value,
                Ttl = Math.Max(ttl, 1)
            };
        }

        public AgentSessionBinding ToBinding(string? eTag) => new(
            new AgentSessionHandle(Id),
            new FoundryAgentSessionId(FoundryAgentSessionId),
            TenantId,
            UserId,
            new FoundryUserIdentity(FoundryUserIdentity),
            AgentName,
            Enum.Parse<AgentSessionProtocolMode>(ProtocolMode),
            CreatedAt,
            LastUsedAt,
            ExpiresAt,
            Enum.Parse<AgentSessionStatus>(Status),
            eTag,
            CorrelationId,
            string.IsNullOrWhiteSpace(FoundryConversationId) ? null : new FoundryConversationId(FoundryConversationId),
            ReplacedBySessionHandle is null ? null : new AgentSessionHandle(ReplacedBySessionHandle));
    }
}
