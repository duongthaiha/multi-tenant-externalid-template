using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Azure.Core;
using Contoso.AssetManagement.FrontendApi.Configuration;
using Microsoft.Extensions.Options;

namespace Contoso.AssetManagement.FrontendApi.Agent;

public sealed class FoundrySessionClient(
    HttpClient httpClient,
    TokenCredential credential,
    IOptions<PortfolioAgentOptions> options,
    ILogger<FoundrySessionClient> logger) : IFoundrySessionClient
{
    private const string HostedAgentsPreviewFeature = "HostedAgents=V1Preview";
    private const string FoundryFeaturesHeader = "Foundry-Features";
    private const string FoundryUserIdentityHeader = "x-ms-user-identity";
    private const string FoundryApiVersion = "v1";
    private static readonly string[] FoundryScopes = ["https://ai.azure.com/.default"];
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private readonly PortfolioAgentOptions options = options.Value;

    public Task<FoundrySessionResult> CreateAsync(
        FoundryUserIdentity? delegatedUserIdentity,
        string correlationId,
        CancellationToken cancellationToken) =>
        SendSessionRequestAsync(
            "create",
            HttpMethod.Post,
            BuildSessionsUri(),
            null,
            delegatedUserIdentity,
            correlationId,
            expectSessionBody: true,
            cancellationToken);

    public Task<FoundrySessionResult> GetAsync(
        FoundryAgentSessionId sessionId,
        FoundryUserIdentity? delegatedUserIdentity,
        string correlationId,
        CancellationToken cancellationToken) =>
        SendSessionRequestAsync(
            "get",
            HttpMethod.Get,
            BuildSessionUri(sessionId),
            sessionId,
            delegatedUserIdentity,
            correlationId,
            expectSessionBody: true,
            cancellationToken);

    public Task<FoundrySessionResult> StopAsync(
        FoundryAgentSessionId sessionId,
        FoundryUserIdentity? delegatedUserIdentity,
        string correlationId,
        CancellationToken cancellationToken) =>
        SendSessionRequestAsync(
            "stop",
            HttpMethod.Post,
            BuildSessionUri(sessionId, ":stop"),
            sessionId,
            delegatedUserIdentity,
            correlationId,
            expectSessionBody: false,
            cancellationToken);

    public Task<FoundrySessionResult> DeleteAsync(
        FoundryAgentSessionId sessionId,
        FoundryUserIdentity? delegatedUserIdentity,
        string correlationId,
        CancellationToken cancellationToken) =>
        SendSessionRequestAsync(
            "delete",
            HttpMethod.Delete,
            BuildSessionUri(sessionId),
            sessionId,
            delegatedUserIdentity,
            correlationId,
            expectSessionBody: false,
            cancellationToken);

    public static Uri? ResolveSessionsEndpoint(PortfolioAgentOptions options)
    {
        ArgumentNullException.ThrowIfNull(options);

        if (options.SessionsEndpoint is not null)
        {
            return EnsureApiVersion(options.SessionsEndpoint);
        }

        if (options.ResponsesEndpoint is null)
        {
            return null;
        }

        var builder = new UriBuilder(options.ResponsesEndpoint);
        var path = builder.Path.TrimEnd('/');
        var endpointIndex = path.IndexOf("/endpoint", StringComparison.OrdinalIgnoreCase);
        if (endpointIndex < 0)
        {
            return null;
        }

        builder.Path = string.Concat(path.AsSpan(0, endpointIndex + "/endpoint".Length), "/sessions");
        builder.Query = string.Empty;
        return EnsureApiVersion(builder.Uri);
    }

    private async Task<FoundrySessionResult> SendSessionRequestAsync(
        string operation,
        HttpMethod method,
        Uri? requestUri,
        FoundryAgentSessionId? sessionId,
        FoundryUserIdentity? delegatedUserIdentity,
        string correlationId,
        bool expectSessionBody,
        CancellationToken cancellationToken)
    {
        if (requestUri is null)
        {
            return FoundrySessionResult.Failure(new FoundrySessionError(
                FoundrySessionErrorKind.Unexpected,
                "foundry-session-endpoint-not-configured",
                null,
                "Foundry sessions endpoint is not configured and could not be derived from the Responses endpoint."));
        }

        try
        {
            var token = await credential.GetTokenAsync(new TokenRequestContext(FoundryScopes), cancellationToken);
            using var message = new HttpRequestMessage(method, requestUri);
            message.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token.Token);
            message.Headers.TryAddWithoutValidation(FoundryFeaturesHeader, HostedAgentsPreviewFeature);
            if (delegatedUserIdentity is not null)
            {
                message.Headers.TryAddWithoutValidation(FoundryUserIdentityHeader, delegatedUserIdentity.Value);
            }

            if (method == HttpMethod.Post)
            {
                message.Content = JsonContent.Create(new FoundrySessionRequest(), options: JsonOptions);
            }

            using var response = await httpClient.SendAsync(message, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                var error = await BuildErrorAsync(response, cancellationToken);
                LogFailure(operation, sessionId, correlationId, error);
                return FoundrySessionResult.Failure(error);
            }

            if (response.StatusCode == HttpStatusCode.NoContent || !expectSessionBody)
            {
                LogSuccess(operation, sessionId, correlationId, response.StatusCode);
                return FoundrySessionResult.Success();
            }

            var session = await ReadSessionAsync(response, cancellationToken);
            if (session is null)
            {
                var error = new FoundrySessionError(
                    FoundrySessionErrorKind.Unexpected,
                    "foundry-session-invalid-response",
                    (int)response.StatusCode,
                    "Foundry session response did not include an agent session id.");
                LogFailure(operation, sessionId, correlationId, error);
                return FoundrySessionResult.Failure(error);
            }

            LogSuccess(operation, session.AgentSessionId, correlationId, response.StatusCode);
            return FoundrySessionResult.Success(session);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            var error = new FoundrySessionError(
                FoundrySessionErrorKind.Transient,
                "foundry-session-timeout",
                (int)HttpStatusCode.GatewayTimeout,
                "Foundry session request timed out.");
            LogFailure(operation, sessionId, correlationId, error);
            return FoundrySessionResult.Failure(error);
        }
        catch (Exception ex) when (ex is HttpRequestException or InvalidOperationException or JsonException)
        {
            var error = new FoundrySessionError(
                FoundrySessionErrorKind.Transient,
                "foundry-session-unavailable",
                (int)HttpStatusCode.BadGateway,
                "Foundry session request failed before a usable response was returned.");
            logger.LogWarning(
                ex,
                "Foundry hosted-session {Operation} failed for correlation {CorrelationId}, sessionHash {SessionHash}, kind {ErrorKind}, code {ErrorCode}.",
                operation,
                correlationId,
                HashForLog(sessionId?.Value),
                error.Kind,
                error.Code);
            return FoundrySessionResult.Failure(error);
        }
    }

    private Uri? BuildSessionsUri() => ResolveSessionsEndpoint(options);

    private Uri? BuildSessionUri(FoundryAgentSessionId sessionId, string suffix = "")
    {
        var sessionsUri = ResolveSessionsEndpoint(options);
        if (sessionsUri is null)
        {
            return null;
        }

        var builder = new UriBuilder(sessionsUri);
        builder.Path = $"{builder.Path.TrimEnd('/')}/{Uri.EscapeDataString(sessionId.Value)}{suffix}";
        return builder.Uri;
    }

    private static async Task<FoundryHostedSession?> ReadSessionAsync(
        HttpResponseMessage response,
        CancellationToken cancellationToken)
    {
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
        var root = document.RootElement;
        var agentSessionId = FindString(root, "agent_session_id")
            ?? FindString(root, "agentSessionId")
            ?? FindString(root, "session_id")
            ?? FindString(root, "sessionId")
            ?? FindString(root, "id");

        if (string.IsNullOrWhiteSpace(agentSessionId))
        {
            return null;
        }

        return new FoundryHostedSession(
            new FoundryAgentSessionId(agentSessionId),
            FindString(root, "status"),
            FindDate(root, "created_at") ?? FindDate(root, "createdAt"),
            FindDate(root, "updated_at") ?? FindDate(root, "updatedAt") ?? FindDate(root, "last_active_at"),
            FindDate(root, "expires_at") ?? FindDate(root, "expiresAt") ?? FindDate(root, "expiration_time"));
    }

    private static async Task<FoundrySessionError> BuildErrorAsync(
        HttpResponseMessage response,
        CancellationToken cancellationToken)
    {
        var (code, message) = await ReadSafeErrorCodeAsync(response, cancellationToken);
        var kind = Classify(response.StatusCode, code, message);
        return new FoundrySessionError(
            kind,
            string.IsNullOrWhiteSpace(code) ? DefaultCode(kind) : code,
            (int)response.StatusCode,
            SafeErrorMessage(kind, response.StatusCode, message));
    }

    private static async Task<(string? Code, string? Message)> ReadSafeErrorCodeAsync(
        HttpResponseMessage response,
        CancellationToken cancellationToken)
    {
        try
        {
            await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
            using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
            var root = document.RootElement;
            return (
                FindString(root, "code") ?? FindString(root, "error_code"),
                FindString(root, "message") ?? FindString(root, "error_description"));
        }
        catch (JsonException)
        {
            return (null, null);
        }
    }

    private static FoundrySessionErrorKind Classify(HttpStatusCode statusCode, string? code, string? message)
    {
        var normalized = $"{code} {message}".ToLowerInvariant();
        if (statusCode == HttpStatusCode.NotFound || normalized.Contains("not_found") || normalized.Contains("not found"))
        {
            return FoundrySessionErrorKind.NotFound;
        }

        if (statusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden
            || normalized.Contains("not_accessible")
            || normalized.Contains("not accessible"))
        {
            return FoundrySessionErrorKind.ForbiddenNotAccessible;
        }

        if (statusCode == HttpStatusCode.Gone
            || normalized.Contains("expired")
            || normalized.Contains("not_running")
            || normalized.Contains("not running"))
        {
            return FoundrySessionErrorKind.ExpiredNotRunning;
        }

        if (statusCode is HttpStatusCode.RequestTimeout or HttpStatusCode.TooManyRequests
            || (int)statusCode >= 500)
        {
            return FoundrySessionErrorKind.Transient;
        }

        if (normalized.Contains("not_ready") || normalized.Contains("not ready"))
        {
            return FoundrySessionErrorKind.Transient;
        }

        return FoundrySessionErrorKind.Unexpected;
    }

    private static string DefaultCode(FoundrySessionErrorKind kind) => kind switch
    {
        FoundrySessionErrorKind.NotFound => "foundry-session-not-found",
        FoundrySessionErrorKind.ForbiddenNotAccessible => "foundry-session-not-accessible",
        FoundrySessionErrorKind.ExpiredNotRunning => "foundry-session-expired-or-not-running",
        FoundrySessionErrorKind.Transient => "foundry-session-transient-error",
        _ => "foundry-session-unexpected-error"
    };

    private static string SafeErrorMessage(FoundrySessionErrorKind kind, HttpStatusCode statusCode, string? message)
    {
        if (!string.IsNullOrWhiteSpace(message) && message.Length <= 200 && !LooksSensitive(message))
        {
            return message;
        }

        return $"{DefaultCode(kind)} ({(int)statusCode}).";
    }

    private static bool LooksSensitive(string value) =>
        value.Contains("Bearer ", StringComparison.OrdinalIgnoreCase)
        || value.Contains("access_token", StringComparison.OrdinalIgnoreCase)
        || value.Contains("refresh_token", StringComparison.OrdinalIgnoreCase)
        || value.Contains("client_secret", StringComparison.OrdinalIgnoreCase);

    private static string? FindString(JsonElement element, string propertyName)
    {
        if (element.ValueKind == JsonValueKind.Object)
        {
            foreach (var property in element.EnumerateObject())
            {
                if (string.Equals(property.Name, propertyName, StringComparison.OrdinalIgnoreCase)
                    && property.Value.ValueKind == JsonValueKind.String)
                {
                    return property.Value.GetString();
                }

                var nested = FindString(property.Value, propertyName);
                if (!string.IsNullOrWhiteSpace(nested))
                {
                    return nested;
                }
            }
        }
        else if (element.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in element.EnumerateArray())
            {
                var nested = FindString(item, propertyName);
                if (!string.IsNullOrWhiteSpace(nested))
                {
                    return nested;
                }
            }
        }

        return null;
    }

    private static DateTimeOffset? FindDate(JsonElement element, string propertyName)
    {
        var value = FindString(element, propertyName);
        return DateTimeOffset.TryParse(value, out var parsed) ? parsed : null;
    }

    private static Uri EnsureApiVersion(Uri uri)
    {
        var builder = new UriBuilder(uri);
        var query = builder.Query.TrimStart('?');
        var hasApiVersion = query
            .Split('&', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Any(part => part.StartsWith("api-version=", StringComparison.OrdinalIgnoreCase));

        if (!hasApiVersion)
        {
            builder.Query = string.IsNullOrWhiteSpace(query)
                ? $"api-version={FoundryApiVersion}"
                : $"{query}&api-version={FoundryApiVersion}";
        }

        return builder.Uri;
    }

    private void LogSuccess(
        string operation,
        FoundryAgentSessionId? sessionId,
        string correlationId,
        HttpStatusCode statusCode) =>
        logger.LogInformation(
            "Foundry hosted-session {Operation} succeeded for correlation {CorrelationId}, status {StatusCode}, sessionHash {SessionHash}.",
            operation,
            correlationId,
            (int)statusCode,
            HashForLog(sessionId?.Value));

    private void LogFailure(
        string operation,
        FoundryAgentSessionId? sessionId,
        string correlationId,
        FoundrySessionError error) =>
        logger.LogWarning(
            "Foundry hosted-session {Operation} failed for correlation {CorrelationId}, status {StatusCode}, sessionHash {SessionHash}, kind {ErrorKind}, code {ErrorCode}.",
            operation,
            correlationId,
            error.StatusCode,
            HashForLog(sessionId?.Value),
            error.Kind,
            error.Code);

    private static string HashForLog(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "none";
        }

        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(value));
        return Convert.ToHexString(hash)[..16].ToLowerInvariant();
    }

    private sealed record FoundrySessionRequest;

    private sealed record FoundrySessionResponse(
        [property: JsonPropertyName("agent_session_id")] string? AgentSessionId,
        [property: JsonPropertyName("status")] string? Status,
        [property: JsonPropertyName("created_at")] DateTimeOffset? CreatedAt,
        [property: JsonPropertyName("updated_at")] DateTimeOffset? UpdatedAt,
        [property: JsonPropertyName("expires_at")] DateTimeOffset? ExpiresAt);
}
