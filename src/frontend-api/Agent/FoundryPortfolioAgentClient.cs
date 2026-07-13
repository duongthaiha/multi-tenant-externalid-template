using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using Azure.Core;
using Contoso.AssetManagement.FrontendApi.Configuration;
using Contoso.AssetManagement.FrontendApi.Models;
using Contoso.AssetManagement.Shared;
using Microsoft.Extensions.Options;

namespace Contoso.AssetManagement.FrontendApi.Agent;

public sealed class FoundryPortfolioAgentClient(
    HttpClient httpClient,
    TokenCredential credential,
    IOptions<PortfolioAgentOptions> options,
    ILogger<FoundryPortfolioAgentClient> logger) : IAgentChatClient
{
    private static readonly string[] FoundryScopes = ["https://ai.azure.com/.default"];
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private readonly PortfolioAgentOptions options = options.Value;

    public async Task<AgentChatResult> AskAsync(
        string tenantId,
        AgentChatRequest request,
        AgentSessionBinding sessionBinding,
        string userAccessToken,
        string serviceToken,
        string correlationId,
        CancellationToken cancellationToken)
    {
        return await AskResponsesAsync(
            tenantId,
            request,
            sessionBinding,
            userAccessToken,
            serviceToken,
            correlationId,
            cancellationToken);
    }

    private async Task<AgentChatResult> AskResponsesAsync(
        string tenantId,
        AgentChatRequest request,
        AgentSessionBinding sessionBinding,
        string userAccessToken,
        string serviceToken,
        string correlationId,
        CancellationToken cancellationToken)
    {
        if (options.ResponsesEndpoint is null)
        {
            return AgentChatResult.Failure(HttpStatusCode.BadGateway, "portfolio-agent-endpoint-not-configured");
        }

        try
        {
            var foundryToken = await credential.GetTokenAsync(new TokenRequestContext(FoundryScopes), cancellationToken);
            using var message = new HttpRequestMessage(HttpMethod.Post, options.ResponsesEndpoint);
            message.Headers.Authorization = new AuthenticationHeaderValue("Bearer", foundryToken.Token);
            message.Headers.TryAddWithoutValidation(TenantConstants.Headers.FoundryUserIdentity, sessionBinding.FoundryUserIdentity.Value);
            AddTrustedContextHeader(message, TenantConstants.Headers.AuthenticatedTenant, tenantId);
            AddTrustedContextHeader(message, TenantConstants.Headers.AuthenticatedUser, sessionBinding.UserId);
            AddTrustedContextHeader(message, TenantConstants.Headers.UserAuthorization, string.Concat("Bearer ", userAccessToken));
            AddTrustedContextHeader(message, TenantConstants.Headers.ServiceAuthorization, string.Concat("Bearer ", serviceToken));
            AddTrustedContextHeader(message, TenantConstants.Headers.CorrelationId, correlationId);
            message.Content = JsonContent.Create(
                new FoundryResponsesRequest(
                    request.Message,
                    false,
                    sessionBinding.FoundryAgentSessionId.Value,
                    sessionBinding.FoundryConversationId is null ? null : new FoundryConversationReference(sessionBinding.FoundryConversationId.Value),
                    new Dictionary<string, string>
                    {
                        ["contoso_tenant_id"] = tenantId,
                        ["contoso_user_id"] = sessionBinding.UserId,
                        ["contoso_correlation_id"] = correlationId
                    }),
                options: JsonOptions);

            using var response = await httpClient.SendAsync(message, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                await LogAgentFailureAsync("responses", response, tenantId, correlationId, cancellationToken);
                return AgentChatResult.Failure(response.StatusCode, "portfolio-agent-error");
            }

            var payload = await response.Content.ReadFromJsonAsync<FoundryResponsesResponse>(JsonOptions, cancellationToken);
            var answer = payload?.OutputText ?? payload?.FindText() ?? "The portfolio agent returned an empty answer.";
            var result = new AgentChatResponse(tenantId, answer, correlationId, sessionBinding.SessionHandle.Value, []);
            return AgentChatResult.Success(response.StatusCode, result);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return AgentChatResult.Failure(HttpStatusCode.GatewayTimeout, "portfolio-agent-timeout");
        }
        catch (Exception ex) when (ex is HttpRequestException or InvalidOperationException or JsonException)
        {
            logger.LogWarning(ex, "Portfolio agent responses call failed for tenant {TenantId}, correlation {CorrelationId}.", tenantId, correlationId);
            return AgentChatResult.Failure(HttpStatusCode.BadGateway, "portfolio-agent-unavailable");
        }
    }

    private Task LogAgentFailureAsync(
        string protocol,
        HttpResponseMessage response,
        string tenantId,
        string correlationId,
        CancellationToken cancellationToken)
    {
        var agentSessionId = response.Headers.TryGetValues("x-agent-session-id", out var sessionValues)
            ? sessionValues.FirstOrDefault()
            : null;
        var requestId = response.Headers.TryGetValues("x-request-id", out var requestValues)
            ? requestValues.FirstOrDefault()
            : null;

        logger.LogWarning(
            "Portfolio agent {Protocol} call failed for tenant {TenantId}, correlation {CorrelationId}, status {StatusCode}, agentSessionId {AgentSessionId}, requestId {RequestId}.",
            protocol,
            tenantId,
            correlationId,
            (int)response.StatusCode,
            agentSessionId ?? "none",
            requestId ?? "none");

        return Task.CompletedTask;
    }

    private static void AddTrustedContextHeader(HttpRequestMessage message, string name, string value)
    {
        message.Headers.TryAddWithoutValidation(name, value);
        message.Headers.TryAddWithoutValidation(TenantConstants.Headers.ClientForwarded(name), value);
    }

    private sealed record FoundryResponsesRequest(
        [property: JsonPropertyName("input")] string Input,
        [property: JsonPropertyName("store")] bool Store,
        [property: JsonPropertyName("agent_session_id")] string AgentSessionId,
        [property: JsonPropertyName("conversation")]
        [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
        FoundryConversationReference? Conversation,
        [property: JsonPropertyName("metadata")] IReadOnlyDictionary<string, string> Metadata);

    private sealed record FoundryConversationReference(
        [property: JsonPropertyName("id")] string Id);

    private sealed record FoundryResponsesResponse(
        [property: JsonPropertyName("output_text")] string? OutputText,
        [property: JsonPropertyName("output")] JsonElement? Output)
    {
        public string? FindText()
        {
            if (Output is not { ValueKind: JsonValueKind.Array } output)
            {
                return null;
            }

            foreach (var item in output.EnumerateArray())
            {
                if (!item.TryGetProperty("content", out var content) || content.ValueKind != JsonValueKind.Array)
                {
                    continue;
                }

                foreach (var part in content.EnumerateArray())
                {
                    if (part.TryGetProperty("text", out var text) && text.ValueKind == JsonValueKind.String)
                    {
                        return text.GetString();
                    }
                }
            }

            return null;
        }
    }
}
