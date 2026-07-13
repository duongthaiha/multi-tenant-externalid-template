using System.Text.Json;

namespace Contoso.AssetManagement.CustomClaimsProvider.Models;

public static class SelectedTenantExtractor
{
    private static readonly string[] TenantHintNames =
    [
        "selectedTenantId",
        "selectedBusinessTenantId",
        "businessTenantId",
        "extension_tenantId"
    ];

    public static string? TryExtract(TokenIssuanceRequest? request)
    {
        if (request?.Data is null)
        {
            return null;
        }

        return TryReadString(request.Data.ExtensionData, TenantHintNames)
            ?? TryReadString(request.Data.AuthenticationContext?.ExtensionData, TenantHintNames)
            ?? TryReadNestedString(request.Data.ExtensionData, "tenantSelection", TenantHintNames)
            ?? TryReadNestedString(request.Data.AuthenticationContext?.ExtensionData, "tenantSelection", TenantHintNames)
            ?? TryReadNestedString(request.Data.ExtensionData, "claims", TenantHintNames)
            ?? TryReadNestedString(request.Data.AuthenticationContext?.ExtensionData, "claims", TenantHintNames)
            ?? TryReadNestedString(request.Data.ExtensionData, "customClaims", TenantHintNames)
            ?? TryReadNestedString(request.Data.AuthenticationContext?.ExtensionData, "customClaims", TenantHintNames);
    }

    private static string? TryReadString(IDictionary<string, JsonElement>? values, IReadOnlyCollection<string> names)
    {
        if (values is null)
        {
            return null;
        }

        foreach (var name in names)
        {
            if (values.TryGetValue(name, out var element))
            {
                var value = ReadString(element);
                if (!string.IsNullOrWhiteSpace(value))
                {
                    return value;
                }
            }
        }

        return null;
    }

    private static string? TryReadNestedString(IDictionary<string, JsonElement>? values, string objectName, IReadOnlyCollection<string> names)
    {
        if (values is null || !values.TryGetValue(objectName, out var parent) || parent.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        foreach (var name in names)
        {
            if (parent.TryGetProperty(name, out var child))
            {
                var value = ReadString(child);
                if (!string.IsNullOrWhiteSpace(value))
                {
                    return value;
                }
            }
        }

        return null;
    }

    private static string? ReadString(JsonElement element) => element.ValueKind switch
    {
        JsonValueKind.String => element.GetString(),
        JsonValueKind.Object when element.TryGetProperty("value", out var value) && value.ValueKind == JsonValueKind.String => value.GetString(),
        _ => null
    };
}
