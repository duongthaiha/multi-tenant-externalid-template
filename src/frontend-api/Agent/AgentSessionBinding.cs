namespace Contoso.AssetManagement.FrontendApi.Agent;

public sealed record AgentSessionBinding(
    AgentSessionHandle SessionHandle,
    FoundryAgentSessionId FoundryAgentSessionId,
    string TenantId,
    string UserId,
    FoundryUserIdentity FoundryUserIdentity,
    string AgentName,
    AgentSessionProtocolMode ProtocolMode,
    DateTimeOffset CreatedAt,
    DateTimeOffset LastUsedAt,
    DateTimeOffset ExpiresAt,
    AgentSessionStatus Status,
    string? ETag = null,
    string? CorrelationId = null,
    FoundryConversationId? FoundryConversationId = null,
    AgentSessionHandle? ReplacedBySessionHandle = null);

public enum AgentSessionStatus
{
    Active,
    Stopped,
    Deleted,
    Expired,
    Replaced
}

public enum AgentSessionProtocolMode
{
    ResponsesV2Delegated,
    InvocationsRollback
}
