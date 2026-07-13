using Contoso.AssetManagement.Shared.Observability;

namespace Contoso.AssetManagement.BackendApi.Observability;

public static class BackendLogger
{
    public static void LogAuthorization(
        ILogger logger,
        LogLevel level,
        string operation,
        string? tenantId,
        string? userId,
        string correlationId,
        string authorizationDecision,
        int statusCode)
    {
        using var scope = logger.BeginScope(new Dictionary<string, object?>
        {
            [LogFields.TenantId] = tenantId ?? "unknown",
            [LogFields.UserId] = userId ?? "unknown",
            [LogFields.CorrelationId] = correlationId,
            [LogFields.Operation] = operation,
            [LogFields.AuthorizationDecision] = authorizationDecision,
            [LogFields.StatusCode] = statusCode
        });

        logger.Log(level, "Backend API authorization decision {authorizationDecision} for {operation}", authorizationDecision, operation);
    }

    public static void LogResult(
        ILogger logger,
        string operation,
        string tenantId,
        string? userId,
        string correlationId,
        string result,
        int statusCode)
    {
        using var scope = logger.BeginScope(new Dictionary<string, object?>
        {
            [LogFields.TenantId] = tenantId,
            [LogFields.UserId] = userId ?? "unknown",
            [LogFields.CorrelationId] = correlationId,
            [LogFields.Operation] = operation,
            [LogFields.Result] = result,
            [LogFields.StatusCode] = statusCode
        });

        logger.LogInformation("Backend API operation result {result} for {operation}", result, operation);
    }
}
