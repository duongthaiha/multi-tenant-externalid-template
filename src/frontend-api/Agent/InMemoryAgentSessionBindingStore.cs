using System.Collections.Concurrent;

namespace Contoso.AssetManagement.FrontendApi.Agent;

public sealed class InMemoryAgentSessionBindingStore : IAgentSessionBindingStore
{
    private readonly ConcurrentDictionary<string, AgentSessionBinding> bindings = new(StringComparer.Ordinal);

    public Task<AgentSessionBinding> CreateAsync(AgentSessionBinding binding, CancellationToken cancellationToken)
    {
        var stored = binding with { ETag = NewETag() };
        if (!bindings.TryAdd(Key(stored.SessionHandle, stored.TenantId, stored.UserId), stored))
        {
            throw new InvalidOperationException("Agent session binding already exists.");
        }

        return Task.FromResult(stored);
    }

    public Task<AgentSessionBinding?> GetOwnedAsync(
        AgentSessionHandle sessionHandle,
        string tenantId,
        string userId,
        string agentName,
        CancellationToken cancellationToken)
    {
        if (bindings.TryGetValue(Key(sessionHandle, tenantId, userId), out var binding)
            && string.Equals(binding.AgentName, agentName, StringComparison.Ordinal)
            && binding.ExpiresAt > DateTimeOffset.UtcNow)
        {
            return Task.FromResult<AgentSessionBinding?>(binding);
        }

        return Task.FromResult<AgentSessionBinding?>(null);
    }

    public Task<AgentSessionBinding> MarkUsedAsync(AgentSessionBinding binding, DateTimeOffset lastUsedAt, DateTimeOffset expiresAt, string correlationId, CancellationToken cancellationToken) =>
        UpsertAsync(binding with { LastUsedAt = lastUsedAt, ExpiresAt = expiresAt, Status = AgentSessionStatus.Active, CorrelationId = correlationId });

    public Task<AgentSessionBinding> MarkStoppedAsync(AgentSessionBinding binding, DateTimeOffset stoppedAt, string correlationId, CancellationToken cancellationToken) =>
        UpsertAsync(binding with { LastUsedAt = stoppedAt, Status = AgentSessionStatus.Stopped, CorrelationId = correlationId });

    public Task<AgentSessionBinding> MarkDeletedAsync(AgentSessionBinding binding, DateTimeOffset deletedAt, string correlationId, CancellationToken cancellationToken) =>
        UpsertAsync(binding with { LastUsedAt = deletedAt, Status = AgentSessionStatus.Deleted, CorrelationId = correlationId });

    public Task<AgentSessionBinding> MarkExpiredAsync(AgentSessionBinding binding, DateTimeOffset expiredAt, string correlationId, CancellationToken cancellationToken) =>
        UpsertAsync(binding with { LastUsedAt = expiredAt, ExpiresAt = expiredAt, Status = AgentSessionStatus.Expired, CorrelationId = correlationId });

    public Task<AgentSessionBinding> MarkReplacedAsync(AgentSessionBinding binding, AgentSessionHandle replacementSessionHandle, DateTimeOffset replacedAt, string correlationId, CancellationToken cancellationToken) =>
        UpsertAsync(binding with { LastUsedAt = replacedAt, Status = AgentSessionStatus.Replaced, ReplacedBySessionHandle = replacementSessionHandle, CorrelationId = correlationId });

    public Task<int> DeleteExpiredAsync(DateTimeOffset expiresBefore, CancellationToken cancellationToken)
    {
        var deleted = 0;
        foreach (var item in bindings)
        {
            if (item.Value.ExpiresAt < expiresBefore && bindings.TryRemove(item.Key, out _))
            {
                deleted++;
            }
        }

        return Task.FromResult(deleted);
    }

    private Task<AgentSessionBinding> UpsertAsync(AgentSessionBinding binding)
    {
        var stored = binding with { ETag = NewETag() };
        bindings[Key(stored.SessionHandle, stored.TenantId, stored.UserId)] = stored;
        return Task.FromResult(stored);
    }

    private static string Key(AgentSessionHandle sessionHandle, string tenantId, string userId) =>
        $"{tenantId}\n{userId}\n{sessionHandle.Value}";

    private static string NewETag() => $"\"{Guid.NewGuid():N}\"";
}
