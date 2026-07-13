namespace Contoso.AssetManagement.BackendApi.Configuration;

using Contoso.AssetManagement.Shared;

public sealed class ServiceAuthOptions
{
    public string HeaderName { get; init; } = TenantConstants.Headers.ServiceAuthorization;
    public string? Authority { get; init; }
    public string? MetadataAddress { get; init; }
    public string? Issuer { get; init; }
    public string[] AdditionalIssuers { get; init; } = [];
    public string? Audience { get; init; }
    public string[] ReadRoles { get; init; } = ["Backend.Read", "Backend.Write"];
    public string[] WriteRoles { get; init; } = ["Backend.Write"];
}
