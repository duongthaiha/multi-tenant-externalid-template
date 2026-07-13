namespace Contoso.AssetManagement.FrontendApi.Configuration;

public sealed class AgentSessionBindingStoreOptions
{
    public Uri? Endpoint { get; init; }
    public string DatabaseName { get; init; } = "bff-agent-sessions";
    public string ContainerName { get; init; } = "sessionBindings";
    public bool UseInMemory { get; init; } = false;
}
