namespace Contoso.AssetManagement.FrontendApi.Agent;

public interface IAgentSessionBindingStore
{
    Task<AgentSessionBinding> CreateAsync(
        AgentSessionBinding binding,
        CancellationToken cancellationToken);

    Task<AgentSessionBinding?> GetOwnedAsync(
        AgentSessionHandle sessionHandle,
        string tenantId,
        string userId,
        string agentName,
        CancellationToken cancellationToken);

    Task<AgentSessionBinding> MarkUsedAsync(
        AgentSessionBinding binding,
        DateTimeOffset lastUsedAt,
        DateTimeOffset expiresAt,
        string correlationId,
        CancellationToken cancellationToken);

    Task<AgentSessionBinding> MarkStoppedAsync(
        AgentSessionBinding binding,
        DateTimeOffset stoppedAt,
        string correlationId,
        CancellationToken cancellationToken);

    Task<AgentSessionBinding> MarkDeletedAsync(
        AgentSessionBinding binding,
        DateTimeOffset deletedAt,
        string correlationId,
        CancellationToken cancellationToken);

    Task<AgentSessionBinding> MarkExpiredAsync(
        AgentSessionBinding binding,
        DateTimeOffset expiredAt,
        string correlationId,
        CancellationToken cancellationToken);

    Task<AgentSessionBinding> MarkReplacedAsync(
        AgentSessionBinding binding,
        AgentSessionHandle replacementSessionHandle,
        DateTimeOffset replacedAt,
        string correlationId,
        CancellationToken cancellationToken);

    Task<int> DeleteExpiredAsync(
        DateTimeOffset expiresBefore,
        CancellationToken cancellationToken);
}

