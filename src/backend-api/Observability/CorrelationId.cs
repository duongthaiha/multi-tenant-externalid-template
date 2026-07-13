using Contoso.AssetManagement.Shared;

namespace Contoso.AssetManagement.BackendApi.Observability;

public static class CorrelationId
{
    public const string HeaderName = TenantConstants.Headers.CorrelationId;

    public static string Resolve(HttpContext context)
    {
        if (context.Request.Headers.TryGetValue(HeaderName, out var values) &&
            !string.IsNullOrWhiteSpace(values.FirstOrDefault()))
        {
            return values.First()!;
        }

        return context.TraceIdentifier;
    }
}
