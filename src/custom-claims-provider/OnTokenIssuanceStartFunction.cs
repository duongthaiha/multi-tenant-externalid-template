using System.Diagnostics;
using System.Net;
using System.Text;
using System.Text.Json;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Azure.Functions.Worker.Http;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Contoso.AssetManagement.CustomClaimsProvider.Models;
using Contoso.AssetManagement.CustomClaimsProvider.Services;
using Contoso.AssetManagement.Shared.Observability;

namespace Contoso.AssetManagement.CustomClaimsProvider;

public sealed class OnTokenIssuanceStartFunction
{
    private static readonly JsonSerializerOptions SerializerOptions = new(JsonSerializerDefaults.Web);
    private const int MaxRequestBytes = 64 * 1024;

    private readonly IEntitlementResolver _entitlementResolver;
    private readonly ControlPlaneCosmosOptions _options;
    private readonly ILogger<OnTokenIssuanceStartFunction> _logger;

    public OnTokenIssuanceStartFunction(
        IEntitlementResolver entitlementResolver,
        IOptions<ControlPlaneCosmosOptions> options,
        ILogger<OnTokenIssuanceStartFunction> logger)
    {
        _entitlementResolver = entitlementResolver;
        _options = options.Value;
        _logger = logger;
    }

    [Function(nameof(OnTokenIssuanceStart))]
    public async Task<HttpResponseData> OnTokenIssuanceStart(
        [HttpTrigger(AuthorizationLevel.Anonymous, "post", Route = "OnTokenIssuanceStart")] HttpRequestData request,
        FunctionContext executionContext)
    {
        var stopwatch = Stopwatch.StartNew();
        var invocationCancellation = executionContext.CancellationToken;
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(invocationCancellation);
        timeout.CancelAfter(TimeSpan.FromMilliseconds(_options.RequestTimeoutMilliseconds));

        TokenIssuanceRequest? payload = null;
        SafeLogContext logContext = new(null, null, null);

        try
        {
            if (TryGetContentLength(request, out var contentLength) && contentLength > MaxRequestBytes)
            {
                return await FailClosedAsync(request, logContext, "request-too-large", stopwatch.ElapsedMilliseconds, timeout.Token);
            }

            payload = await JsonSerializer.DeserializeAsync<TokenIssuanceRequest>(request.Body, SerializerOptions, timeout.Token);
            logContext = CreateLogContext(payload);

            var userId = payload?.Data?.AuthenticationContext?.User?.Id;
            var email = payload?.Data?.AuthenticationContext?.User?.Mail
                ?? payload?.Data?.AuthenticationContext?.User?.UserPrincipalName;
            var resourceAppId = payload?.Data?.AuthenticationContext?.ResourceServicePrincipal?.AppId;
            var selectedTenantId = SelectedTenantExtractor.TryExtract(payload);

            if (string.IsNullOrWhiteSpace(userId) || string.IsNullOrWhiteSpace(resourceAppId))
            {
                return await FailClosedAsync(request, logContext, "invalid-callback-payload", stopwatch.ElapsedMilliseconds, timeout.Token);
            }

            var result = await _entitlementResolver.ResolveAsync(userId, email, resourceAppId, selectedTenantId, timeout.Token);
            if (!result.Succeeded || result.Claims is null)
            {
                return await FailClosedAsync(request, logContext with { TenantId = selectedTenantId }, result.Decision, stopwatch.ElapsedMilliseconds, timeout.Token);
            }

            var response = await WriteJsonAsync(
                request,
                HttpStatusCode.OK,
                TokenIssuanceResponses.ProvideClaims(result.Claims.TenantId, result.Claims.Roles, result.Claims.TenantStatus),
                invocationCancellation);

            LogDecision(logContext with { TenantId = result.Claims.TenantId }, "allowed", "success", stopwatch.ElapsedMilliseconds, LogLevel.Information);
            return response;
        }
        catch (OperationCanceledException) when (!invocationCancellation.IsCancellationRequested)
        {
            return await FailClosedAsync(request, logContext, "timeout", stopwatch.ElapsedMilliseconds, CancellationToken.None);
        }
        catch (JsonException)
        {
            return await FailClosedAsync(request, logContext, "invalid-json", stopwatch.ElapsedMilliseconds, CancellationToken.None);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("Claims provider dependency failure: {ExceptionType}: {ExceptionMessage}", ex.GetType().Name, ex.Message);
            return await FailClosedAsync(request, logContext, "dependency-error", stopwatch.ElapsedMilliseconds, CancellationToken.None);
        }
    }

    private async Task<HttpResponseData> FailClosedAsync(
        HttpRequestData request,
        SafeLogContext context,
        string decision,
        long latencyMilliseconds,
        CancellationToken cancellationToken)
    {
        LogDecision(context, decision, "fail-closed", latencyMilliseconds, LogLevel.Warning);
        return await WriteJsonAsync(request, HttpStatusCode.OK, TokenIssuanceResponses.EmptyClaims(), cancellationToken);
    }

    private void LogDecision(SafeLogContext context, string decision, string result, long latencyMilliseconds, LogLevel level)
    {
        using var scope = _logger.BeginScope(new Dictionary<string, object?>
        {
            [LogFields.CorrelationId] = context.CorrelationId,
            [LogFields.UserId] = context.UserId,
            [LogFields.TenantId] = context.TenantId,
            [LogFields.Operation] = nameof(OnTokenIssuanceStart),
            [LogFields.AuthorizationDecision] = decision,
            [LogFields.Result] = result,
            ["latencyMs"] = latencyMilliseconds
        });

        _logger.Log(level, "Custom claims provider completed with decision {AuthorizationDecision} and result {Result} in {LatencyMs}ms", decision, result, latencyMilliseconds);
    }

    private static SafeLogContext CreateLogContext(TokenIssuanceRequest? payload) =>
        new(
            payload?.Data?.AuthenticationContext?.CorrelationId,
            payload?.Data?.AuthenticationContext?.User?.Id,
            SelectedTenantExtractor.TryExtract(payload));

    private static async Task<HttpResponseData> WriteJsonAsync<T>(
        HttpRequestData request,
        HttpStatusCode statusCode,
        T body,
        CancellationToken cancellationToken)
    {
        var response = request.CreateResponse(statusCode);
        response.Headers.Add("Content-Type", "application/json; charset=utf-8");
        var payload = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(body, SerializerOptions));
        await response.Body.WriteAsync(payload, cancellationToken);
        return response;
    }

    private static bool TryGetContentLength(HttpRequestData request, out long contentLength)
    {
        contentLength = 0;
        return request.Headers.TryGetValues("Content-Length", out var values)
            && long.TryParse(values.FirstOrDefault(), out contentLength);
    }

    private sealed record SafeLogContext(string? CorrelationId, string? UserId, string? TenantId);
}
