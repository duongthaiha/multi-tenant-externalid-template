using Contoso.AssetManagement.FrontendApi.Models;

namespace Contoso.AssetManagement.FrontendApi.Agent;

public interface IAgentChatClient
{
    Task<AgentChatResult> AskAsync(
        string tenantId,
        AgentChatRequest request,
        AgentSessionBinding sessionBinding,
        string userAccessToken,
        string serviceToken,
        string correlationId,
        CancellationToken cancellationToken);
}
