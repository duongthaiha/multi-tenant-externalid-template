using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Azure.Core;
using Contoso.AssetManagement.FrontendApi.Configuration;
using Microsoft.Extensions.Options;

namespace Contoso.AssetManagement.FrontendApi.Agent;

public sealed class FoundryConversationClient(
    HttpClient httpClient,
    TokenCredential credential,
    IOptions<PortfolioAgentOptions> options,
    ILogger<FoundryConversationClient> logger) : IFoundryConversationClient
{
    private const string HostedAgentsPreviewFeature = "HostedAgents=V1Preview";
    private const string FoundryFeaturesHeader = "Foundry-Features";
    private const string FoundryUserIdentityHeader = "x-ms-user-identity";
    private const string FoundryApiVersion = "v1";
    private static readonly string[] FoundryScopes = ["https://ai.azure.com/.default"];
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private readonly PortfolioAgentOptions options = options.Value;

    public async Task<FoundryConversationResult> CreateAsync(
        FoundryUserIdentity delegatedUserIdentity,
        string correlationId,
        CancellationToken cancellationToken)
    {
        var requestUri = ResolveConversationsEndpoint(options);
        if (requestUri is null)
        {
            return FoundryConversationResult.Failure(new FoundrySessionError(
                FoundrySessionErrorKind.Unexpected,
                "foundry-conversation-endpoint-not-configured",
                null,
                "Foundry conversations endpoint is not configured and could not be derived from the Responses endpoint."));
        }

        try
        {
            var token = await credential.GetTokenAsync(new TokenRequestContext(FoundryScopes), cancellationToken);
            using var message = new HttpRequestMessage(HttpMethod.Post, requestUri);
            message.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token.Token);
            message.Headers.TryAddWithoutValidation(FoundryFeaturesHeader, HostedAgentsPreviewFeature);
            message.Headers.TryAddWithoutValidation(FoundryUserIdentityHeader, delegatedUserIdentity.Value);
            message.Content = JsonContent.Create(new FoundryConversationRequest(), options: JsonOptions);

            using var response = await httpClient.SendAsync(message, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                var error = await BuildErrorAsync(response, cancellationToken);
                LogFailure(correlationId, delegatedUserIdentity, error);
                return FoundryConversationResult.Failure(error);
            }

            var conversationId = await ReadConversationIdAsync(response, cancellationToken);
            if (conversationId is null)
            {
                var error = new FoundrySessionError(
                    FoundrySessionErrorKind.Unexpected,
                    "foundry-conversation-invalid-response",
                    (int)response.StatusCode,
                    "Foundry conversation response did not include an id.");
                LogFailure(correlationId, delegatedUserIdentity, error);
                return FoundryConversationResult.Failure(error);
            }

            logger.LogInformation(
                "Foundry conversation create succeeded for correlation {CorrelationId}, status {StatusCode}, userHash {UserHash}.",
                correlationId,
                (int)response.StatusCode,
                HashForLog(delegatedUserIdentity.Value));
            return FoundryConversationResult.Success(conversationId);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            var error = new FoundrySessionError(
                FoundrySessionErrorKind.Transient,
                "foundry-conversation-timeout",
                (int)HttpStatusCode.GatewayTimeout,
                "Foundry conversation request timed out.");
            LogFailure(correlationId, delegatedUserIdentity, error);
            return FoundryConversationResult.Failure(error);
        }
        catch (Exception ex) when (ex is HttpRequestException or InvalidOperationException or JsonException)
        {
            var error = new FoundrySessionError(
                FoundrySessionErrorKind.Transient,
                "foundry-conversation-unavailable",
                (int)HttpStatusCode.BadGateway,
                "Foundry conversation request failed before a usable response was returned.");
            logger.LogWarning(
                ex,
                "Foundry conversation create failed for correlation {CorrelationId}, userHash {UserHash}, kind {ErrorKind}, code {ErrorCode}.",
                correlationId,
                HashForLog(delegatedUserIdentity.Value),
                error.Kind,
                error.Code);
            return FoundryConversationResult.Failure(error);
        }
    }

    public static Uri? ResolveConversationsEndpoint(PortfolioAgentOptions options)
    {
        ArgumentNullException.ThrowIfNull(options);

        if (options.ConversationsEndpoint is not null)
        {
            return EnsureApiVersion(options.ConversationsEndpoint);
        }

        if (options.ResponsesEndpoint is null)
        {
            return null;
        }

        var builder = new UriBuilder(options.ResponsesEndpoint);
        var path = builder.Path.TrimEnd('/');
        var responsesIndex = path.LastIndexOf("/responses", StringComparison.OrdinalIgnoreCase);
        if (responsesIndex < 0)
        {
            return null;
        }

        builder.Path = string.Concat(path.AsSpan(0, responsesIndex), "/conversations");
        builder.Query = string.Empty;
        return EnsureApiVersion(builder.Uri);
    }

    private static async Task<FoundryConversationId?> ReadConversationIdAsync(
        HttpResponseMessage response,
        CancellationToken cancellationToken)
    {
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
        var id = FindString(document.RootElement, "id")
            ?? FindString(document.RootElement, "conversation_id")
            ?? FindString(document.RootElement, "conversationId");

        return string.IsNullOrWhiteSpace(id) ? null : new FoundryConversationId(id);
    }

    private static async Task<FoundrySessionError> BuildErrorAsync(
        HttpResponseMessage response,
        CancellationToken cancellationToken)
    {
        var (code, message) = await ReadSafeErrorCodeAsync(response, cancellationToken);
        var kind = (int)response.StatusCode >= 500 || response.StatusCode == HttpStatusCode.TooManyRequests
            ? FoundrySessionErrorKind.Transient
            : FoundrySessionErrorKind.Unexpected;
        return new FoundrySessionError(
            kind,
            string.IsNullOrWhiteSpace(code) ? DefaultCode(kind) : code,
            (int)response.StatusCode,
            string.IsNullOrWhiteSpace(message) ? DefaultCode(kind) : message);
    }

    private static async Task<(string? Code, string? Message)> ReadSafeErrorCodeAsync(
        HttpResponseMessage response,
        CancellationToken cancellationToken)
    {
        try
        {
            await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
            using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
            return (
                FindString(document.RootElement, "code") ?? FindString(document.RootElement, "error_code"),
                FindString(document.RootElement, "message") ?? FindString(document.RootElement, "error_description"));
        }
        catch (JsonException)
        {
            return (null, null);
        }
    }

    private static string DefaultCode(FoundrySessionErrorKind kind) => kind switch
    {
        FoundrySessionErrorKind.Transient => "foundry-conversation-transient-error",
        _ => "foundry-conversation-unexpected-error"
    };

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

    private void LogFailure(
        string correlationId,
        FoundryUserIdentity delegatedUserIdentity,
        FoundrySessionError error) =>
        logger.LogWarning(
            "Foundry conversation create failed for correlation {CorrelationId}, status {StatusCode}, userHash {UserHash}, kind {ErrorKind}, code {ErrorCode}.",
            correlationId,
            error.StatusCode,
            HashForLog(delegatedUserIdentity.Value),
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

    private sealed record FoundryConversationRequest;
}
