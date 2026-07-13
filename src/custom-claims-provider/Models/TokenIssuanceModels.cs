using System.Text.Json;
using System.Text.Json.Serialization;

namespace Contoso.AssetManagement.CustomClaimsProvider.Models;

public sealed class TokenIssuanceRequest
{
    [JsonPropertyName("type")]
    public string? Type { get; init; }

    [JsonPropertyName("source")]
    public string? Source { get; init; }

    [JsonPropertyName("data")]
    public TokenIssuanceData? Data { get; init; }
}

public sealed class TokenIssuanceData
{
    [JsonPropertyName("@odata.type")]
    public string? ODataType { get; init; }

    [JsonPropertyName("tenantId")]
    public string? TenantId { get; init; }

    [JsonPropertyName("authenticationContext")]
    public AuthenticationContext? AuthenticationContext { get; init; }

    [JsonExtensionData]
    public IDictionary<string, JsonElement>? ExtensionData { get; init; }
}

public sealed class AuthenticationContext
{
    [JsonPropertyName("correlationId")]
    public string? CorrelationId { get; init; }

    [JsonPropertyName("protocol")]
    public string? Protocol { get; init; }

    [JsonPropertyName("clientServicePrincipal")]
    public ServicePrincipalContext? ClientServicePrincipal { get; init; }

    [JsonPropertyName("resourceServicePrincipal")]
    public ServicePrincipalContext? ResourceServicePrincipal { get; init; }

    [JsonPropertyName("user")]
    public TokenIssuanceUser? User { get; init; }

    [JsonExtensionData]
    public IDictionary<string, JsonElement>? ExtensionData { get; init; }
}

public sealed class ServicePrincipalContext
{
    [JsonPropertyName("appId")]
    public string? AppId { get; init; }

    [JsonPropertyName("appDisplayName")]
    public string? AppDisplayName { get; init; }
}

public sealed class TokenIssuanceUser
{
    [JsonPropertyName("id")]
    public string? Id { get; init; }

    [JsonPropertyName("mail")]
    public string? Mail { get; init; }

    [JsonPropertyName("userPrincipalName")]
    public string? UserPrincipalName { get; init; }

    [JsonPropertyName("userType")]
    public string? UserType { get; init; }

    [JsonExtensionData]
    public IDictionary<string, JsonElement>? ExtensionData { get; init; }
}

public sealed record TokenIssuanceResponse(
    [property: JsonPropertyName("data")] TokenIssuanceResponseData Data);

public sealed record TokenIssuanceResponseData(
    [property: JsonPropertyName("@odata.type")] string ODataType,
    [property: JsonPropertyName("actions")] IReadOnlyCollection<ProvideClaimsAction> Actions);

public sealed record ProvideClaimsAction(
    [property: JsonPropertyName("@odata.type")] string ODataType,
    [property: JsonPropertyName("claims")] TokenClaims Claims);

public sealed record TokenClaims(
    [property: JsonPropertyName("extension_tenantId")] string TenantId,
    [property: JsonPropertyName("tenant_roles")] IReadOnlyCollection<string> Roles,
    [property: JsonPropertyName("tenant_status")] string TenantStatus);

public static class TokenIssuanceResponses
{
    private const string ResponseDataType = "microsoft.graph.onTokenIssuanceStartResponseData";
    private const string ProvideClaimsType = "microsoft.graph.tokenIssuanceStart.provideClaimsForToken";

    public static TokenIssuanceResponse ProvideClaims(string tenantId, IReadOnlyCollection<string> roles, string tenantStatus) =>
        new(new TokenIssuanceResponseData(
            ResponseDataType,
            [new ProvideClaimsAction(ProvideClaimsType, new TokenClaims(tenantId, roles, tenantStatus))]));

    public static TokenIssuanceResponse EmptyClaims() =>
        new(new TokenIssuanceResponseData(ResponseDataType, []));
}
