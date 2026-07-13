namespace Contoso.AssetManagement.FrontendApi.Agent;

public sealed record FoundryAgentSessionId
{
    public FoundryAgentSessionId(string value)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(value);
        Value = value;
    }

    public string Value { get; }

    public override string ToString() => Value;
}

