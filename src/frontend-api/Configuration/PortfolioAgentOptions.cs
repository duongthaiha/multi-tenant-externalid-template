namespace Contoso.AssetManagement.FrontendApi.Configuration;

public sealed class PortfolioAgentOptions
{
    public string AgentName { get; init; } = "portfolio-agent";
    public Uri? ResponsesEndpoint { get; init; }
    public Uri? ConversationsEndpoint { get; init; }
    public Uri? SessionsEndpoint { get; init; }
    public Uri? InvocationsEndpoint { get; init; }
    public bool UseResponsesV2 { get; init; } = true;
    public bool UseInvocations { get; init; } = false;
    public TimeSpan SessionTtl { get; init; } = TimeSpan.FromHours(4);
    public bool ValidateSessionBeforeInvoke { get; init; } = true;
    public TimeSpan Timeout { get; init; } = TimeSpan.FromSeconds(60);
}
