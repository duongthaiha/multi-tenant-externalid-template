namespace Contoso.AssetManagement.FrontendApi.Agent;

public interface IFoundryConversationClient
{
    Task<FoundryConversationResult> CreateAsync(
        FoundryUserIdentity delegatedUserIdentity,
        string correlationId,
        CancellationToken cancellationToken);
}

public sealed record FoundryConversationResult(
    bool IsSuccess,
    FoundryConversationId? ConversationId,
    FoundrySessionError? Error)
{
    public static FoundryConversationResult Success(FoundryConversationId conversationId) =>
        new(true, conversationId, null);

    public static FoundryConversationResult Failure(FoundrySessionError error) =>
        new(false, null, error);
}
