namespace Contoso.AssetManagement.FrontendApi.Agent;

public interface IFoundrySessionClient
{
    Task<FoundrySessionResult> CreateAsync(
        FoundryUserIdentity? delegatedUserIdentity,
        string correlationId,
        CancellationToken cancellationToken);

    Task<FoundrySessionResult> GetAsync(
        FoundryAgentSessionId sessionId,
        FoundryUserIdentity? delegatedUserIdentity,
        string correlationId,
        CancellationToken cancellationToken);

    Task<FoundrySessionResult> StopAsync(
        FoundryAgentSessionId sessionId,
        FoundryUserIdentity? delegatedUserIdentity,
        string correlationId,
        CancellationToken cancellationToken);

    Task<FoundrySessionResult> DeleteAsync(
        FoundryAgentSessionId sessionId,
        FoundryUserIdentity? delegatedUserIdentity,
        string correlationId,
        CancellationToken cancellationToken);
}

public sealed record FoundryHostedSession(
    FoundryAgentSessionId AgentSessionId,
    string? Status,
    DateTimeOffset? CreatedAt,
    DateTimeOffset? UpdatedAt,
    DateTimeOffset? ExpiresAt);

public sealed record FoundrySessionResult(
    bool IsSuccess,
    FoundryHostedSession? Session,
    FoundrySessionError? Error)
{
    public static FoundrySessionResult Success(FoundryHostedSession? session = null) =>
        new(true, session, null);

    public static FoundrySessionResult Failure(FoundrySessionError error) =>
        new(false, null, error);
}

public sealed record FoundrySessionError(
    FoundrySessionErrorKind Kind,
    string Code,
    int? StatusCode,
    string Message);

public enum FoundrySessionErrorKind
{
    NotFound,
    ForbiddenNotAccessible,
    ExpiredNotRunning,
    Transient,
    Unexpected
}
