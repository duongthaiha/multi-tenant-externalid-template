namespace Contoso.AssetManagement.FrontendApi.Agent;

public sealed record FoundryUserIdentity
{
    public FoundryUserIdentity(string value)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(value);
        Value = value;
    }

    public string Value { get; }

    public override string ToString() => Value;
}

