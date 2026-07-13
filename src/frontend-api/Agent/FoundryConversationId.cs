namespace Contoso.AssetManagement.FrontendApi.Agent;

public sealed record FoundryConversationId
{
    public FoundryConversationId(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new ArgumentException("Foundry conversation id is required.", nameof(value));
        }

        Value = value;
    }

    public string Value { get; }
}
