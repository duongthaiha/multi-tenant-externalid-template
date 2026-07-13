using System.Diagnostics;
using System.Diagnostics.Metrics;
using System.Collections.Concurrent;
using System.Net;
using System.Security.Cryptography;
using System.Text.Json;
using Azure.AI.AgentServer.Core;
using Azure.AI.AgentServer.Invocations;
using Azure.AI.AgentServer.Responses;
using Azure.AI.AgentServer.Responses.Models;
using Azure.AI.Projects;
using Azure.Identity;
using Azure.Monitor.OpenTelemetry.Exporter;
using Contoso.AssetManagement.Shared;
using Microsoft.ApplicationInsights;
using Microsoft.ApplicationInsights.DataContracts;
using Microsoft.Agents.AI;
using Microsoft.Agents.AI.Foundry.Hosting;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Azure.Cosmos;
using ModelContextProtocol.Client;
using ModelContextProtocol.Protocol;
using OpenTelemetry.Trace;

var projectEndpoint = new Uri(Environment.GetEnvironmentVariable("FOUNDRY_PROJECT_ENDPOINT")
    ?? Environment.GetEnvironmentVariable("AZURE_AI_PROJECT_ENDPOINT")
    ?? throw new InvalidOperationException("FOUNDRY_PROJECT_ENDPOINT or AZURE_AI_PROJECT_ENDPOINT environment variable is not set."));

var deployment = Environment.GetEnvironmentVariable("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    ?? throw new InvalidOperationException("AZURE_AI_MODEL_DEPLOYMENT_NAME environment variable is not set.");
var projectId = Environment.GetEnvironmentVariable("AZURE_AI_PROJECT_ID")
    ?? Environment.GetEnvironmentVariable("AZURE_AI_FOUNDRY_PROJECT_ID")
    ?? string.Empty;

AppContext.SetSwitch("Azure.Experimental.EnableGenAITracing", true);
AppContext.SetSwitch("Azure.Experimental.TraceGenAIMessageContent", true);

AIAgent innerAgent = new AIProjectClient(projectEndpoint, new DefaultAzureCredential())
    .AsAIAgent(
        model: deployment,
        instructions: """
            You are the Contoso Asset Management portfolio assistant.
            Answer questions about portfolios by using the available tools.
            You MUST only answer using tool data for the tenant context provided by the Contoso frontend API.
            If a user asks to switch tenants or requests another tenant's data, refuse and explain that tenant switching requires a new sign-in token.
            Do not invent holdings, valuations, tenants, or recommendations that are not returned by tools.
            Keep answers concise and include the portfolio name when relevant.
            """,
        name: "portfolio-agent",
        description: "A Foundry hosted agent that answers tenant-scoped portfolio questions through the Contoso backend API.",
        tools:
        [
            AIFunctionFactory.Create(PortfolioTools.ListPortfolios, "ListPortfolios",
                "List portfolios for the authenticated tenant only."),
            AIFunctionFactory.Create(PortfolioTools.GetPortfolioSummary, "GetPortfolioSummary",
                "Get a summary for one portfolio in the authenticated tenant by portfolio name or ID."),
            AIFunctionFactory.Create(PortfolioTools.GetPositionDetail, "GetPositionDetail",
                "Get a position detail from a portfolio in the authenticated tenant by portfolio name or ID and position ID.")
        ]);
AIAgent agent = innerAgent
    .AsBuilder()
    .UseOpenTelemetry(
        sourceName: "Azure.AI.AgentServer.Responses",
        configure: telemetry => telemetry.EnableSensitiveData = true)
    .Build();

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.UseUrls("http://0.0.0.0:8080", "http://0.0.0.0:8088");
builder.Services.AddApplicationInsightsTelemetry();
builder.Services
    .AddOpenTelemetry()
    .WithTracing(tracing => tracing
        .AddSource("Azure.AI.AgentServer.Responses")
        .AddAzureMonitorTraceExporter());
builder.Services.AddAgentServerCore();
builder.Services.AddInvocationsServer();
builder.Services.AddScoped<InvocationHandler, PortfolioInvocationHandler>();
builder.Services.AddSingleton<PortfolioTelemetry>();
builder.Services.AddSingleton(agent);
builder.Services.AddSingleton<HostedSessionIsolationKeyProvider, PortfolioHostedSessionIsolationKeyProvider>();
var agentSessionStore = PortfolioToolContext.CosmosAgentSessionStore.CreateFromEnvironment();
builder.Services.AddSingleton<AgentSessionStore>(agentSessionStore);
builder.Services.AddFoundryResponses(agent, agentSessionStore);

var app = builder.Build();

app.Services.GetRequiredService<PortfolioTelemetry>().LogAgentStarting(projectEndpoint.Host, deployment);

app.MapGet("/health", () => Results.Ok(new { status = "healthy" }));
app.UseAgentServerCore();

app.Use(async (context, next) =>
{
    var startTime = DateTimeOffset.UtcNow;
    var success = true;
    var telemetry = context.RequestServices.GetRequiredService<PortfolioTelemetry>();
    PortfolioToolContextAccessor.Current = PortfolioToolContext.FromRequest(context);
    PortfolioToolContextAccessor.Telemetry = telemetry;
    try
    {
        await next();
        success = context.Response.StatusCode < 500;
    }
    catch
    {
        success = false;
        throw;
    }
    finally
    {
        telemetry.TrackFoundryProjectScope(projectId, startTime, DateTimeOffset.UtcNow - startTime, success);
        PortfolioToolContextAccessor.Current = null;
        PortfolioToolContextAccessor.ClearTelemetry();
    }
});

app.MapFoundryResponses();
app.MapInvocationsServer();

app.Run();

internal static class PortfolioTools
{
    public static async Task<string> ListPortfolios()
    {
        var context = RequireToolContext(nameof(ListPortfolios));
        var telemetry = PortfolioToolContextAccessor.Telemetry;
        using var activity = telemetry.StartToolActivity(nameof(ListPortfolios), context.TenantId, context.CorrelationId);
        telemetry.RecordToolInvocation(nameof(ListPortfolios));
        telemetry.LogToolInvocation(nameof(ListPortfolios), context.TenantId, "all");

        var result = await PortfolioBackendClient.ListPortfoliosAsync(context);
        if (!result.IsSuccess || result.Value is null)
        {
            telemetry.RecordToolMiss(nameof(ListPortfolios));
            return ToToolFailure(result.StatusCode, "I could not list portfolios for the authenticated tenant.");
        }

        if (result.Value.Count == 0)
        {
            return "No portfolios were found for the authenticated tenant.";
        }

        return string.Join(Environment.NewLine, result.Value.Select(portfolio =>
            $"{portfolio.Id}: {portfolio.Name} - total value {FormatMoney(portfolio.MarketValue, portfolio.Currency)} as of {portfolio.AsOfDate:yyyy-MM-dd}."));
    }

    public static async Task<string> GetPortfolioSummary(string portfolio)
    {
        var context = RequireToolContext(nameof(GetPortfolioSummary));
        var telemetry = PortfolioToolContextAccessor.Telemetry;
        using var activity = telemetry.StartToolActivity(nameof(GetPortfolioSummary), context.TenantId, context.CorrelationId);
        telemetry.RecordToolInvocation(nameof(GetPortfolioSummary));
        telemetry.LogToolInvocation(nameof(GetPortfolioSummary), context.TenantId, portfolio);

        var result = await PortfolioBackendClient.ListPortfoliosAsync(context);
        if (!result.IsSuccess || result.Value is null)
        {
            telemetry.RecordToolMiss(nameof(GetPortfolioSummary));
            return ToToolFailure(result.StatusCode, $"I could not retrieve portfolio '{portfolio}' for the authenticated tenant.");
        }

        var match = FindPortfolio(result.Value, portfolio);
        if (match is null)
        {
            telemetry.RecordToolMiss(nameof(GetPortfolioSummary));
            return $"No portfolio matched '{portfolio}' for the authenticated tenant. Use ListPortfolios to see available portfolio IDs and names.";
        }

        return $"""
            Portfolio: {match.Name}
            ID: {match.Id}
            Tenant: {match.TenantId}
            Currency: {match.Currency}
            Market value: {FormatMoney(match.MarketValue, match.Currency)}
            As of: {match.AsOfDate:yyyy-MM-dd}
            """;
    }

    public static async Task<string> GetPositionDetail(string portfolio, string positionId)
    {
        var context = RequireToolContext(nameof(GetPositionDetail));
        var telemetry = PortfolioToolContextAccessor.Telemetry;
        using var activity = telemetry.StartToolActivity(nameof(GetPositionDetail), context.TenantId, context.CorrelationId);
        telemetry.RecordToolInvocation(nameof(GetPositionDetail));
        telemetry.LogToolInvocation(nameof(GetPositionDetail), context.TenantId, $"{portfolio}:{positionId}");

        var portfolios = await PortfolioBackendClient.ListPortfoliosAsync(context);
        if (!portfolios.IsSuccess || portfolios.Value is null)
        {
            telemetry.RecordToolMiss(nameof(GetPositionDetail));
            return ToToolFailure(portfolios.StatusCode, $"I could not retrieve portfolio '{portfolio}' for the authenticated tenant.");
        }

        var match = FindPortfolio(portfolios.Value, portfolio);
        if (match is null)
        {
            telemetry.RecordToolMiss(nameof(GetPositionDetail));
            return $"No portfolio matched '{portfolio}' for the authenticated tenant.";
        }

        var position = await PortfolioBackendClient.GetPositionAsync(context, match.Id, positionId);
        if (!position.IsSuccess || position.Value is null)
        {
            telemetry.RecordToolMiss(nameof(GetPositionDetail));
            return position.StatusCode == HttpStatusCode.NotFound
                ? $"Portfolio '{match.Name}' does not contain a position with ID '{positionId}' for the authenticated tenant."
                : ToToolFailure(position.StatusCode, $"I could not retrieve position '{positionId}' for the authenticated tenant.");
        }

        return $"""
            Portfolio: {match.Name}
            Position ID: {position.Value.Id}
            Instrument: {position.Value.InstrumentName}
            Asset class: {position.Value.AssetClass}
            Quantity: {position.Value.Quantity:N2}
            Market value: {FormatUsd(position.Value.MarketValue)}
            """;
    }

    private static PortfolioToolContext RequireToolContext(string toolName)
    {
        var context = PortfolioToolContextAccessor.Current;
        if (context is not null && context.IsComplete)
        {
            return context;
        }

        if (PortfolioToolContextCache.TryGetCurrentRunContext(out context))
        {
            PortfolioToolContextAccessor.Current = context;
            return context;
        }

        if (PortfolioToolContextCache.TryGetLatest(out context))
        {
            PortfolioToolContextAccessor.Current = context;
            return context;
        }

        var telemetry = PortfolioToolContextAccessor.Telemetry;
        telemetry.LogMissingToolContext(toolName, context);
        telemetry.RecordToolMiss(toolName);
        throw new InvalidOperationException("Portfolio agent tools require BFF-provided tenant, user token, service token, and correlation context.");
    }

    private static Portfolio? FindPortfolio(IEnumerable<Portfolio> portfolios, string value)
    {
        return portfolios.FirstOrDefault(portfolio =>
            portfolio.Id.Equals(value, StringComparison.OrdinalIgnoreCase)
            || portfolio.Name.Equals(value, StringComparison.OrdinalIgnoreCase)
            || portfolio.Name.Contains(value, StringComparison.OrdinalIgnoreCase));
    }

    private static string ToToolFailure(HttpStatusCode statusCode, string message) => statusCode switch
    {
        HttpStatusCode.Forbidden => "The backend denied this portfolio request for the authenticated tenant.",
        HttpStatusCode.Unauthorized => "The backend could not authenticate this portfolio request.",
        _ => message
    };

    private static string FormatMoney(decimal value, string currency) => FormattableString.Invariant($"{currency} {value:N0}");

    private static string FormatUsd(decimal value) => FormattableString.Invariant($"USD {value:N0}");

}

internal sealed class PortfolioInvocationHandler(
    AIAgent agent,
    PortfolioTelemetry telemetry,
    ILogger<PortfolioInvocationHandler> logger) : InvocationHandler
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public override async Task HandleAsync(
        HttpRequest request,
        HttpResponse response,
        InvocationContext context,
        CancellationToken cancellationToken)
    {
        PortfolioInvocationRequest? payload;
        try
        {
            payload = await JsonSerializer.DeserializeAsync<PortfolioInvocationRequest>(
                request.Body,
                JsonOptions,
                cancellationToken);
        }
        catch (JsonException)
        {
            await WriteErrorAsync(response, StatusCodes.Status400BadRequest, "invalid-json", context.InvocationId, cancellationToken);
            return;
        }

        var validationError = Validate(payload);
        if (validationError is not null)
        {
            await WriteErrorAsync(response, StatusCodes.Status400BadRequest, validationError, context.InvocationId, cancellationToken);
            return;
        }

        var toolContext = new PortfolioToolContext(
            payload!.TenantId,
            payload.UserId,
            payload.UserAccessToken,
            payload.ServiceToken,
            payload.CorrelationId);
        var conversationId = string.IsNullOrWhiteSpace(payload.ConversationId)
            ? context.SessionId
            : payload.ConversationId;

        PortfolioToolContextAccessor.Current = toolContext;
        PortfolioToolContextAccessor.Telemetry = telemetry;
        PortfolioToolContextCache.Store(payload.UserId, toolContext);

        try
        {
            var agentResponse = await agent.RunAsync(payload.Message, session: null, options: null, cancellationToken);

            response.StatusCode = StatusCodes.Status200OK;
            response.ContentType = "application/json";
            await JsonSerializer.SerializeAsync(
                response.Body,
                new PortfolioInvocationResponse(
                    payload.TenantId,
                    agentResponse.Text ?? "The portfolio agent returned an empty answer.",
                    payload.CorrelationId,
                    conversationId,
                    []),
                JsonOptions,
                cancellationToken);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            await WriteErrorAsync(response, StatusCodes.Status504GatewayTimeout, "portfolio-agent-timeout", context.InvocationId, cancellationToken);
        }
        catch (Exception ex) when (ex is InvalidOperationException or JsonException or HttpRequestException)
        {
            logger.LogWarning(
                ex,
                "Portfolio invocation failed for tenant {TenantId}, correlation {CorrelationId}, invocation {InvocationId}.",
                payload.TenantId,
                payload.CorrelationId,
                context.InvocationId);
            await WriteErrorAsync(response, StatusCodes.Status502BadGateway, "portfolio-agent-unavailable", context.InvocationId, cancellationToken);
        }
        finally
        {
            PortfolioToolContextAccessor.Current = null;
            PortfolioToolContextAccessor.ClearTelemetry();
        }
    }

    public override Task GetOpenApiAsync(HttpRequest request, HttpResponse response, CancellationToken cancellationToken)
    {
        response.StatusCode = StatusCodes.Status200OK;
        response.ContentType = "application/json";
        return response.WriteAsync(InvocationOpenApi, cancellationToken);
    }

    private static string? Validate(PortfolioInvocationRequest? request)
    {
        if (request is null)
        {
            return "request-required";
        }

        if (string.IsNullOrWhiteSpace(request.Message))
        {
            return "message-required";
        }

        if (string.IsNullOrWhiteSpace(request.TenantId)
            || string.IsNullOrWhiteSpace(request.UserId)
            || string.IsNullOrWhiteSpace(request.UserAccessToken)
            || string.IsNullOrWhiteSpace(request.ServiceToken)
            || string.IsNullOrWhiteSpace(request.CorrelationId))
        {
            return "trusted-context-required";
        }

        return null;
    }

    private static Task WriteErrorAsync(
        HttpResponse response,
        int statusCode,
        string error,
        string invocationId,
        CancellationToken cancellationToken)
    {
        response.StatusCode = statusCode;
        response.ContentType = "application/json";
        return JsonSerializer.SerializeAsync(
            response.Body,
            new PortfolioInvocationError(error, invocationId),
            JsonOptions,
            cancellationToken);
    }

    private const string InvocationOpenApi = """
        {
          "openapi": "3.0.3",
          "info": {
            "title": "Contoso Portfolio Agent Invocations",
            "version": "1.0.0"
          },
          "paths": {
            "/invocations": {
              "post": {
                "operationId": "chatWithPortfolioAgent",
                "requestBody": {
                  "required": true,
                  "content": {
                    "application/json": {
                      "schema": { "$ref": "#/components/schemas/PortfolioInvocationRequest" }
                    }
                  }
                },
                "responses": {
                  "200": {
                    "description": "Portfolio agent answer",
                    "content": {
                      "application/json": {
                        "schema": { "$ref": "#/components/schemas/PortfolioInvocationResponse" }
                      }
                    }
                  }
                }
              }
            }
          },
          "components": {
            "schemas": {
              "PortfolioInvocationRequest": {
                "type": "object",
                "required": ["message", "tenantId", "userId", "userAccessToken", "serviceToken", "correlationId"],
                "properties": {
                  "message": { "type": "string" },
                  "tenantId": { "type": "string" },
                  "userId": { "type": "string" },
                  "userAccessToken": { "type": "string" },
                  "serviceToken": { "type": "string" },
                  "correlationId": { "type": "string" },
                  "conversationId": { "type": "string", "nullable": true }
                }
              },
              "PortfolioInvocationResponse": {
                "type": "object",
                "required": ["tenantId", "answer", "correlationId"],
                "properties": {
                  "tenantId": { "type": "string" },
                  "answer": { "type": "string" },
                  "correlationId": { "type": "string" },
                  "conversationId": { "type": "string", "nullable": true },
                  "citations": { "type": "array", "items": { "type": "object" } }
                }
              }
            }
          }
        }
        """;
}

internal sealed record PortfolioInvocationRequest(
    string Message,
    string TenantId,
    string UserId,
    string UserAccessToken,
    string ServiceToken,
    string CorrelationId,
    string? ConversationId);

internal sealed record PortfolioInvocationResponse(
    string TenantId,
    string Answer,
    string CorrelationId,
    string? ConversationId,
    IReadOnlyList<AgentToolResult> Citations);

internal sealed record PortfolioInvocationError(string Error, string InvocationId);

internal sealed record AgentToolResult(string ToolName, string Result);

internal sealed record PortfolioToolContext(
    string TenantId,
    string UserId,
    string UserAccessToken,
    string ServiceToken,
    string CorrelationId)
{
    public const string TenantHeaderName = TenantConstants.Headers.AuthenticatedTenant;
    public const string UserHeaderName = TenantConstants.Headers.AuthenticatedUser;
    public const string UserAuthorizationHeaderName = TenantConstants.Headers.UserAuthorization;
    public const string ServiceAuthorizationHeaderName = TenantConstants.Headers.ServiceAuthorization;
    public const string CorrelationHeaderName = TenantConstants.Headers.CorrelationId;

    public bool IsComplete =>
        !string.IsNullOrWhiteSpace(TenantId)
        && !string.IsNullOrWhiteSpace(UserId)
        && !string.IsNullOrWhiteSpace(UserAccessToken)
        && !string.IsNullOrWhiteSpace(ServiceToken)
        && !string.IsNullOrWhiteSpace(CorrelationId);

    public static PortfolioToolContext FromRequest(HttpContext context)
    {
        var tenantId = context.Request.Headers[TenantHeaderName].FirstOrDefault() ?? string.Empty;
        var userId = context.Request.Headers[UserHeaderName].FirstOrDefault() ?? string.Empty;
        var userAccessToken = ExtractBearer(context.Request.Headers[UserAuthorizationHeaderName].FirstOrDefault())
            ?? ExtractBearer(context.Request.Headers.Authorization.FirstOrDefault());
        var serviceToken = ExtractBearer(context.Request.Headers[ServiceAuthorizationHeaderName].FirstOrDefault());
        var correlationId = context.Request.Headers[CorrelationHeaderName].FirstOrDefault();

        return new PortfolioToolContext(
            tenantId,
            userId,
            userAccessToken ?? string.Empty,
            serviceToken ?? string.Empty,
            string.IsNullOrWhiteSpace(correlationId) ? Guid.NewGuid().ToString("N") : correlationId);
    }

    public static PortfolioToolContext FromClientHeaders(IReadOnlyDictionary<string, string> clientHeaders)
    {
        var tenantId = GetClientHeader(clientHeaders, TenantHeaderName) ?? string.Empty;
        var userId = GetClientHeader(clientHeaders, UserHeaderName) ?? string.Empty;
        var userAccessToken = ExtractBearer(GetClientHeader(clientHeaders, UserAuthorizationHeaderName));
        var serviceToken = ExtractBearer(GetClientHeader(clientHeaders, ServiceAuthorizationHeaderName));
        var correlationId = GetClientHeader(clientHeaders, CorrelationHeaderName);

        return new PortfolioToolContext(
            tenantId,
            userId,
            userAccessToken ?? string.Empty,
            serviceToken ?? string.Empty,
            string.IsNullOrWhiteSpace(correlationId) ? Guid.NewGuid().ToString("N") : correlationId);
    }

    public static PortfolioToolContext FromMetadata(Metadata? metadata)
    {
        var values = metadata?.AdditionalProperties;
        if (values is null)
        {
            return new PortfolioToolContext(string.Empty, string.Empty, string.Empty, string.Empty, Guid.NewGuid().ToString("N"));
        }

        values.TryGetValue("contoso_tenant_id", out var tenantId);
        values.TryGetValue("contoso_user_id", out var userId);
        values.TryGetValue("contoso_user_access_token", out var userAccessToken);
        values.TryGetValue("contoso_service_token", out var serviceToken);
        values.TryGetValue("contoso_correlation_id", out var correlationId);

        return new PortfolioToolContext(
            tenantId ?? string.Empty,
            userId ?? string.Empty,
            userAccessToken ?? string.Empty,
            serviceToken ?? string.Empty,
            string.IsNullOrWhiteSpace(correlationId) ? Guid.NewGuid().ToString("N") : correlationId);
    }

    public static PortfolioToolContext FromMetadataAndClientHeaders(
        Metadata? metadata,
        IReadOnlyDictionary<string, string> clientHeaders)
    {
        var metadataContext = FromMetadata(metadata);
        var headerContext = FromClientHeaders(clientHeaders);

        return new PortfolioToolContext(
            FirstPresent(metadataContext.TenantId, headerContext.TenantId),
            FirstPresent(metadataContext.UserId, headerContext.UserId),
            FirstPresent(headerContext.UserAccessToken, metadataContext.UserAccessToken),
            FirstPresent(headerContext.ServiceToken, metadataContext.ServiceToken),
            FirstPresent(metadataContext.CorrelationId, headerContext.CorrelationId));
    }

    private static string? GetClientHeader(IReadOnlyDictionary<string, string> clientHeaders, string headerName)
    {
        if (clientHeaders.TryGetValue(headerName, out var value)
            || clientHeaders.TryGetValue(headerName.ToLowerInvariant(), out value)
            || clientHeaders.TryGetValue(TenantConstants.Headers.ClientForwarded(headerName), out value)
            || clientHeaders.TryGetValue(TenantConstants.Headers.ClientForwarded(headerName.ToLowerInvariant()), out value))
        {
            return value;
        }

        return null;
    }

    private static string FirstPresent(string primary, string fallback) =>
        string.IsNullOrWhiteSpace(primary) ? fallback : primary;

    internal sealed class CosmosAgentSessionStore(
        CosmosClient cosmosClient,
        AgentMemoryOptions options) : AgentSessionStore
    {
        private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

        public override async ValueTask SaveSessionAsync(
            AIAgent agent,
            string conversationId,
            AgentSession session,
            string userId,
            CancellationToken cancellationToken)
        {
            var context = RequireContext();
            var resolvedUserId = string.IsNullOrWhiteSpace(context.UserId) ? userId : context.UserId;
            var document = new AgentSessionDocument(
                Id: CreateDocumentId(agent, conversationId, context.TenantId, resolvedUserId),
                TenantId: context.TenantId,
                DocumentType: "AgentSession",
                AgentName: ResolveAgentName(agent),
                UserId: resolvedUserId,
                ConversationId: conversationId,
                StateBag: session.StateBag.Serialize(),
                CreatedAt: DateTimeOffset.UtcNow,
                UpdatedAt: DateTimeOffset.UtcNow,
                Ttl: options.SessionTtlSeconds);

            var container = GetContainer(context.TenantId);
            try
            {
                await container.UpsertItemAsync(document, new PartitionKey(context.TenantId), cancellationToken: cancellationToken);
            }
            catch (CosmosException exception)
            {
                PortfolioToolContextAccessor.Telemetry.LogCosmosSessionStoreFailure("save", context.TenantId, exception);
            }
        }

        public override async ValueTask<AgentSession?> GetSessionAsync(
            AIAgent agent,
            string conversationId,
            string userId,
            CancellationToken cancellationToken)
        {
            var context = PortfolioToolContextAccessor.Current;
            if (context is null || string.IsNullOrWhiteSpace(context.TenantId))
            {
                return null;
            }

            var resolvedUserId = string.IsNullOrWhiteSpace(context.UserId) ? userId : context.UserId;
            var container = GetContainer(context.TenantId);
            try
            {
                var response = await container.ReadItemAsync<AgentSessionDocument>(
                    CreateDocumentId(agent, conversationId, context.TenantId, resolvedUserId),
                    new PartitionKey(context.TenantId),
                    cancellationToken: cancellationToken);
                return new StoredAgentSession(AgentSessionStateBag.Deserialize(response.Resource.StateBag));
            }
            catch (CosmosException exception) when (exception.StatusCode == HttpStatusCode.NotFound)
            {
                return null;
            }
            catch (CosmosException exception)
            {
                PortfolioToolContextAccessor.Telemetry.LogCosmosSessionStoreFailure("read", context.TenantId, exception);
                return null;
            }
        }

        public static CosmosAgentSessionStore CreateFromEnvironment()
        {
            var options = AgentMemoryOptions.FromEnvironment();
            var credential = new DefaultAzureCredential();
            var client = new CosmosClient(options.Endpoint, credential, new CosmosClientOptions
            {
                ConnectionMode = ConnectionMode.Gateway,
                SerializerOptions = new CosmosSerializationOptions
                {
                    PropertyNamingPolicy = CosmosPropertyNamingPolicy.CamelCase
                }
            });
            return new CosmosAgentSessionStore(client, options);
        }

        private Container GetContainer(string tenantId)
        {
            var database = string.Concat(options.DatabasePrefix, NormalizeTenantId(tenantId));
            return cosmosClient.GetContainer(database, options.ContainerName);
        }

        private static PortfolioToolContext RequireContext()
        {
            var context = PortfolioToolContextAccessor.Current;
            if (context is null || string.IsNullOrWhiteSpace(context.TenantId))
            {
                throw new InvalidOperationException("Portfolio agent session persistence requires trusted tenant context.");
            }

            return context;
        }

        private static string CreateDocumentId(AIAgent agent, string conversationId, string tenantId, string userId)
        {
            var raw = $"{ResolveAgentName(agent)}|{tenantId}|{userId}|{conversationId}";
            var hash = Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(raw))).ToLowerInvariant();
            return $"agent-session-{hash}";
        }

        private static string ResolveAgentName(AIAgent agent) =>
            string.IsNullOrWhiteSpace(agent.Name) ? "portfolio-agent" : agent.Name;

        private static string NormalizeTenantId(string tenantId) =>
            tenantId.Replace("-", string.Empty, StringComparison.Ordinal).ToLowerInvariant();

    }

    internal sealed record AgentSessionDocument(
        string Id,
        string TenantId,
        string DocumentType,
        string AgentName,
        string UserId,
        string ConversationId,
        JsonElement StateBag,
        DateTimeOffset CreatedAt,
        DateTimeOffset UpdatedAt,
        int Ttl);

    private sealed class StoredAgentSession(AgentSessionStateBag stateBag) : AgentSession(stateBag);

    internal sealed record AgentMemoryOptions(
        string Endpoint,
        string DatabasePrefix,
        string ContainerName,
        int SessionTtlSeconds)
    {
        public static AgentMemoryOptions FromEnvironment()
        {
            var endpoint = Environment.GetEnvironmentVariable("AgentMemory__Endpoint");
            if (string.IsNullOrWhiteSpace(endpoint))
            {
                throw new InvalidOperationException("AgentMemory__Endpoint environment variable is not set.");
            }

            return new AgentMemoryOptions(
                endpoint,
                Environment.GetEnvironmentVariable("AgentMemory__DatabasePrefix") ?? "agent-memory-",
                Environment.GetEnvironmentVariable("AgentMemory__ContainerName") ?? "agentSessions",
                int.TryParse(Environment.GetEnvironmentVariable("AgentMemory__SessionTtlSeconds"), out var ttl) ? ttl : 2592000);
        }
    }

    private static string? ExtractBearer(string? value)
    {
        if (string.IsNullOrWhiteSpace(value) || !value.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
        {
            return null;
        }

        return value["Bearer ".Length..].Trim();
    }
}

#pragma warning disable MAAI001
internal sealed class PortfolioHostedSessionIsolationKeyProvider(
    PortfolioTelemetry telemetry) : HostedSessionIsolationKeyProvider
{
    public override ValueTask<HostedSessionContext?> GetKeysAsync(
        ResponseContext context,
        CreateResponse request,
        CancellationToken cancellationToken)
    {
        var toolContext = PortfolioToolContext.FromMetadataAndClientHeaders(request.Metadata, context.ClientHeaders);
        PortfolioToolContextAccessor.Current = toolContext;
        PortfolioToolContextAccessor.Telemetry = telemetry;

        var userId = !string.IsNullOrWhiteSpace(toolContext.UserId)
            ? toolContext.UserId
            : context.PlatformContext.UserIdKey;

        if (!string.IsNullOrWhiteSpace(userId) && toolContext.IsComplete)
        {
            PortfolioToolContextCache.Store(userId, toolContext);
        }

        return ValueTask.FromResult<HostedSessionContext?>(
            string.IsNullOrWhiteSpace(userId) ? null : new HostedSessionContext(userId));
    }
}
#pragma warning restore MAAI001

internal static class PortfolioToolContextCache
{
    private static readonly TimeSpan TimeToLive = TimeSpan.FromMinutes(15);
    private static readonly ConcurrentDictionary<string, CachedContext> Contexts = new(StringComparer.Ordinal);

    public static void Store(string userId, PortfolioToolContext context)
    {
        RemoveExpired();
        Contexts[userId] = new CachedContext(context, DateTimeOffset.UtcNow);
        Latest = new CachedContext(context, DateTimeOffset.UtcNow);
    }

    public static bool TryGetCurrentRunContext(out PortfolioToolContext? context)
    {
        context = null;
        var hostedContext = AIAgent.CurrentRunContext?.Session.GetHostedContext();
        if (hostedContext is null)
        {
            return false;
        }

        if (!Contexts.TryGetValue(hostedContext.UserId, out var cached)
            || DateTimeOffset.UtcNow - cached.UpdatedAt > TimeToLive
            || !cached.Context.IsComplete)
        {
            return false;
        }

        context = cached.Context;
        return true;
    }

    private static void RemoveExpired()
    {
        var now = DateTimeOffset.UtcNow;
        foreach (var item in Contexts)
        {
            if (now - item.Value.UpdatedAt > TimeToLive)
            {
                Contexts.TryRemove(item.Key, out _);
            }
        }

        if (Latest is { } latest && now - latest.UpdatedAt > TimeToLive)
        {
            Latest = null;
        }
    }

    public static bool TryGetLatest(out PortfolioToolContext? context)
    {
        context = null;
        var latest = Latest;
        if (latest is null
            || DateTimeOffset.UtcNow - latest.UpdatedAt > TimeToLive
            || !latest.Context.IsComplete)
        {
            return false;
        }

        context = latest.Context;
        return true;
    }

    private static CachedContext? Latest;

    private sealed record CachedContext(PortfolioToolContext Context, DateTimeOffset UpdatedAt);
}

internal static class PortfolioToolContextAccessor
{
    private static readonly AsyncLocal<PortfolioToolContext?> CurrentContext = new();

    public static PortfolioToolContext? Current
    {
        get => CurrentContext.Value;
        set => CurrentContext.Value = value;
    }

    public static PortfolioTelemetry Telemetry
    {
        get => CurrentTelemetry.Value ?? FallbackTelemetry;
        set => CurrentTelemetry.Value = value;
    }

    public static void ClearTelemetry()
    {
        CurrentTelemetry.Value = null;
    }

    private static readonly PortfolioTelemetry FallbackTelemetry = new(NullLogger<PortfolioTelemetry>.Instance);
    private static readonly AsyncLocal<PortfolioTelemetry?> CurrentTelemetry = new();
}

internal static class PortfolioBackendClient
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private static readonly Uri McpServerUri = ResolveMcpServerUri();
    private const string ApimMcpProtocolVersion = "2025-06-18";

    public static Task<BackendToolResult<IReadOnlyList<Portfolio>>> ListPortfoliosAsync(PortfolioToolContext context) =>
        CallMcpToolAsync<IReadOnlyList<Portfolio>>(
            context,
            "listPortfolios",
            new Dictionary<string, object?> { ["tenantId"] = context.TenantId });

    public static Task<BackendToolResult<Position>> GetPositionAsync(PortfolioToolContext context, string portfolioId, string positionId) =>
        CallMcpToolAsync<Position>(
            context,
            "getPositionDetail",
            new Dictionary<string, object?>
            {
                ["tenantId"] = context.TenantId,
                ["portfolioId"] = portfolioId,
                ["positionId"] = positionId
            });

    private static async Task<BackendToolResult<T>> CallMcpToolAsync<T>(
        PortfolioToolContext context,
        string toolName,
        IReadOnlyDictionary<string, object?> arguments)
    {
        try
        {
            await using var mcpClient = await CreateMcpClientAsync(context);
            var result = await mcpClient.CallToolAsync(toolName, arguments);
            if (result.IsError == true)
            {
                PortfolioToolContextAccessor.Telemetry.LogMcpToolFailure(toolName, context.TenantId, context.CorrelationId, "mcp-result-error");
                return BackendToolResult<T>.Failure(HttpStatusCode.BadGateway);
            }

            var value = DeserializeToolResult<T>(result);
            if (value is null)
            {
                PortfolioToolContextAccessor.Telemetry.LogMcpToolFailure(toolName, context.TenantId, context.CorrelationId, "empty-or-unparseable-result");
            }

            return value is null
                ? BackendToolResult<T>.Failure(HttpStatusCode.BadGateway)
                : BackendToolResult<T>.Success(value);
        }
        catch (HttpRequestException ex) when (ex.StatusCode is not null)
        {
            PortfolioToolContextAccessor.Telemetry.LogMcpToolException(
                toolName,
                context.TenantId,
                context.CorrelationId,
                ex.GetType().Name,
                ex.StatusCode.Value.ToString());
            return BackendToolResult<T>.Failure(ex.StatusCode.Value);
        }
        catch (Exception ex) when (ex is InvalidOperationException or JsonException or TaskCanceledException)
        {
            PortfolioToolContextAccessor.Telemetry.LogMcpToolException(
                toolName,
                context.TenantId,
                context.CorrelationId,
                ex.GetType().Name,
                ex.Message ?? string.Empty);
            return BackendToolResult<T>.Failure(HttpStatusCode.BadGateway);
        }
    }

    private static Task<McpClient> CreateMcpClientAsync(PortfolioToolContext context)
    {
        var headers = new Dictionary<string, string>
        {
            ["Authorization"] = $"Bearer {context.UserAccessToken}",
            [PortfolioToolContext.CorrelationHeaderName] = context.CorrelationId,
            [TenantConstants.Headers.AgentId] = "portfolio-agent"
        };
        headers["Authorization"] = string.Concat("Bearer ", context.UserAccessToken);

        var transport = new HttpClientTransport(
            new HttpClientTransportOptions
            {
                Endpoint = McpServerUri,
                Name = "contoso_backend_mcp",
                TransportMode = HttpTransportMode.StreamableHttp,
                AdditionalHeaders = headers
            });

        var options = new McpClientOptions
        {
            ProtocolVersion = ApimMcpProtocolVersion
        };

        return McpClient.CreateAsync(transport, options);
    }

    private static T? DeserializeToolResult<T>(CallToolResult result)
    {
        if (result.StructuredContent is { } structured)
        {
            return structured.Deserialize<T>(JsonOptions);
        }

        var text = string.Join(
            Environment.NewLine,
            result.Content.OfType<TextContentBlock>().Select(content => content.Text));
        return string.IsNullOrWhiteSpace(text)
            ? default
            : JsonSerializer.Deserialize<T>(text, JsonOptions);
    }

    private static Uri ResolveMcpServerUri()
    {
        var mcpServerUrl = Environment.GetEnvironmentVariable("BACKEND_MCP_SERVER_URL");
        if (!string.IsNullOrWhiteSpace(mcpServerUrl))
        {
            return new Uri(mcpServerUrl);
        }

        var baseAddress = Environment.GetEnvironmentVariable("BACKEND_API_BASE_URL");
        if (!string.IsNullOrWhiteSpace(baseAddress))
        {
            return new Uri(baseAddress);
        }

        throw new InvalidOperationException("BACKEND_MCP_SERVER_URL environment variable is not set.");
    }

#if false
    private static async Task<BackendToolResult<T>> SendAsync<T>(PortfolioToolContext context, HttpMethod method, string path)
    {
        using var request = new HttpRequestMessage(method, path);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", context.UserAccessToken);
        request.Headers.TryAddWithoutValidation(PortfolioToolContext.ServiceAuthorizationHeaderName, $"Bearer {context.ServiceToken}");
        request.Headers.TryAddWithoutValidation(PortfolioToolContext.CorrelationHeaderName, context.CorrelationId);

        using var response = await HttpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead);
        if (!response.IsSuccessStatusCode)
        {
            return BackendToolResult<T>.Failure(response.StatusCode);
        }

        var value = await response.Content.ReadFromJsonAsync<T>(JsonOptions);
        return value is null
            ? BackendToolResult<T>.Failure(HttpStatusCode.BadGateway)
            : BackendToolResult<T>.Success(value);
    }

    private static HttpClient CreateHttpClient()
    {
        var baseAddress = Environment.GetEnvironmentVariable("BACKEND_API_BASE_URL");
        if (string.IsNullOrWhiteSpace(baseAddress))
        {
            throw new InvalidOperationException("BACKEND_API_BASE_URL environment variable is not set.");
        }

        return new HttpClient
        {
            BaseAddress = new Uri(baseAddress),
            Timeout = TimeSpan.FromSeconds(10)
        };
    }

    private static string Escape(string value) => Uri.EscapeDataString(value);
#endif
}

internal sealed record BackendToolResult<T>(bool IsSuccess, HttpStatusCode StatusCode, T? Value)
{
    public static BackendToolResult<T> Success(T value) => new(true, HttpStatusCode.OK, value);

    public static BackendToolResult<T> Failure(HttpStatusCode statusCode) => new(false, statusCode, default);
}

internal sealed class PortfolioTelemetry(
    ILogger<PortfolioTelemetry> logger,
    TelemetryClient? telemetryClient = null)
{
    private static readonly ActivitySource ActivitySource = new("Contoso.AssetManagement.PortfolioAgent");
    private static readonly Meter Meter = new("Contoso.AssetManagement.PortfolioAgent");
    private static readonly Counter<long> ToolInvocationCounter = Meter.CreateCounter<long>(
        "portfolio_agent_tool_invocations",
        description: "Number of portfolio-agent tool invocations.");
    private static readonly Counter<long> ToolMissCounter = Meter.CreateCounter<long>(
        "portfolio_agent_tool_misses",
        description: "Number of portfolio-agent tool lookups that did not match demo data.");
    private const string FoundryProjectIdProperty = "microsoft.foundry.project.id";
    private const string GenAiProjectIdProperty = "gen_ai.azure_ai_project.id";
    private const string AgentNameProperty = "gen_ai.agent.name";
    private const string AgentName = "portfolio-agent";

    public void LogAgentStarting(string projectHost, string modelDeployment)
    {
        logger.LogInformation(
            "Portfolio agent starting with Foundry project host {ProjectHost} and model deployment {ModelDeployment}.",
            projectHost,
            modelDeployment);
    }

    public Activity? StartToolActivity(string toolName, string tenantId, string correlationId)
    {
        var activity = ActivitySource.StartActivity($"portfolio-agent.tool.{toolName}");
        activity?.SetTag(AgentNameProperty, AgentName);
        ApplyFoundryProjectMetadata(null, (name, value) => activity?.SetTag(name, value));
        activity?.SetTag("portfolio_agent.tool.name", toolName);
        activity?.SetTag("tenantId", tenantId);
        activity?.SetTag("correlationId", correlationId);
        return activity;
    }

    public void TrackFoundryProjectScope(
        string projectId,
        DateTimeOffset startTime,
        TimeSpan duration,
        bool success)
    {
        ApplyFoundryProjectMetadata(projectId, (name, value) => Activity.Current?.SetTag(name, value));

        if (telemetryClient is null || string.IsNullOrWhiteSpace(projectId))
        {
            return;
        }

        var marker = new DependencyTelemetry
        {
            Type = "InProc",
            Target = AgentName,
            Name = "foundry-project-scope",
            Data = "portal-trace-project-scope",
            Timestamp = startTime,
            Duration = duration,
            Success = success
        };
        ApplyFoundryProjectMetadata(projectId, (name, value) => marker.Properties[name] = value);
        marker.Properties[AgentNameProperty] = AgentName;

        telemetryClient.TrackDependency(marker);
    }

    private static void ApplyFoundryProjectMetadata(string? projectId, Action<string, string> apply)
    {
        projectId ??= Environment.GetEnvironmentVariable("AZURE_AI_PROJECT_ID")
            ?? Environment.GetEnvironmentVariable("AZURE_AI_FOUNDRY_PROJECT_ID");
        if (string.IsNullOrWhiteSpace(projectId))
        {
            return;
        }

        apply(FoundryProjectIdProperty, projectId);
        apply(GenAiProjectIdProperty, projectId);
    }

    public void RecordToolInvocation(string toolName)
    {
        ToolInvocationCounter.Add(1, new KeyValuePair<string, object?>("tool.name", toolName));
    }

    public void RecordToolMiss(string toolName)
    {
        ToolMissCounter.Add(1, new KeyValuePair<string, object?>("tool.name", toolName));
    }

    public void LogToolInvocation(string toolName, string tenantId, string lookup)
    {
        logger.LogInformation(
            "Portfolio tool {ToolName} invoked for tenant {TenantId} and lookup {Lookup}.",
            toolName,
            tenantId,
            lookup);
    }

    public void LogMissingToolContext(string toolName, PortfolioToolContext? context)
    {
        logger.LogWarning(
            "Portfolio tool {ToolName} missing trusted context. tenant:{TenantPresent} user:{UserPresent} userToken:{UserTokenPresent} serviceToken:{ServiceTokenPresent} correlation:{CorrelationPresent}.",
            toolName,
            HasValue(context?.TenantId),
            HasValue(context?.UserId),
            HasValue(context?.UserAccessToken),
            HasValue(context?.ServiceToken),
            HasValue(context?.CorrelationId));
    }

    public void LogMcpToolFailure(string toolName, string tenantId, string correlationId, string reason)
    {
        logger.LogWarning(
            "Portfolio MCP tool {ToolName} failed for tenant {TenantId}, correlation {CorrelationId}, reason {Reason}.",
            toolName,
            tenantId,
            correlationId,
            reason);
    }

    public void LogMcpToolException(string toolName, string tenantId, string correlationId, string exceptionType, string message)
    {
        logger.LogWarning(
            "Portfolio MCP tool {ToolName} exception for tenant {TenantId}, correlation {CorrelationId}: {ExceptionType}: {ExceptionMessage}.",
            toolName,
            tenantId,
            correlationId,
            exceptionType,
            message);
    }

    public void LogCosmosSessionStoreFailure(string operation, string tenantId, CosmosException exception)
    {
        logger.LogWarning(
            exception,
            "Portfolio agent Cosmos session store {Operation} failed for tenant {TenantId}. Status {StatusCode}, substatus {SubStatusCode}, activity {ActivityId}.",
            operation,
            tenantId,
            (int)exception.StatusCode,
            exception.SubStatusCode,
            exception.ActivityId);
    }

    private static string HasValue(string? value) => string.IsNullOrWhiteSpace(value) ? "missing" : "present";
}
