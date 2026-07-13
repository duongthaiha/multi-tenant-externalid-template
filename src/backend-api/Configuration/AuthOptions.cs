namespace Contoso.AssetManagement.BackendApi.Configuration;

public sealed class AuthOptions
{
    public string? Authority { get; init; }
    public string? MetadataAddress { get; init; }
    public string Issuer { get; init; } = string.Empty;
    public string[] AdditionalIssuers { get; init; } = [];
    public string Audience { get; init; } = string.Empty;
    public string[] AdditionalAudiences { get; init; } = [];
}
