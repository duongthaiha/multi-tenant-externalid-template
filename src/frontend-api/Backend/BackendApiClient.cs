using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Contoso.AssetManagement.FrontendApi.Configuration;
using Contoso.AssetManagement.FrontendApi.Observability;
using Contoso.AssetManagement.Shared;
using Microsoft.Extensions.Options;

namespace Contoso.AssetManagement.FrontendApi.Backend;

public sealed class BackendApiClient(
    HttpClient httpClient,
    IBackendServiceTokenProvider serviceTokenProvider,
    IOptions<BackendApiOptions> options,
    ILogger<BackendApiClient> logger) : IBackendApiClient
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private readonly BackendApiOptions options = options.Value;

    public Task<BackendResult<IReadOnlyList<Portfolio>>> ListPortfoliosAsync(
        string tenantId,
        string userAccessToken,
        string correlationId,
        CancellationToken cancellationToken) =>
        SendAsync<IReadOnlyList<Portfolio>>(
            HttpMethod.Get,
            $"/internal/tenants/{Escape(tenantId)}/portfolios",
            userAccessToken,
            correlationId,
            cancellationToken);

    public Task<BackendResult<Position>> GetPositionAsync(
        string tenantId,
        string portfolioId,
        string positionId,
        string userAccessToken,
        string correlationId,
        CancellationToken cancellationToken) =>
        SendAsync<Position>(
            HttpMethod.Get,
            $"/internal/tenants/{Escape(tenantId)}/portfolios/{Escape(portfolioId)}/positions/{Escape(positionId)}",
            userAccessToken,
            correlationId,
            cancellationToken);

    public Task<BackendResult<TransactionApproval>> ApproveTransactionAsync(
        string tenantId,
        string transactionId,
        string userAccessToken,
        string correlationId,
        CancellationToken cancellationToken) =>
        SendAsync<TransactionApproval>(
            HttpMethod.Post,
            $"/internal/tenants/{Escape(tenantId)}/transactions/{Escape(transactionId)}/approve",
            userAccessToken,
            correlationId,
            cancellationToken);

    private async Task<BackendResult<T>> SendAsync<T>(
        HttpMethod method,
        string path,
        string userAccessToken,
        string correlationId,
        CancellationToken cancellationToken)
    {
        try
        {
            using var request = new HttpRequestMessage(method, path);
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", userAccessToken);
            request.Headers.TryAddWithoutValidation(CorrelationId.HeaderName, correlationId);

            var serviceToken = await serviceTokenProvider.GetServiceTokenAsync(cancellationToken);
            request.Headers.TryAddWithoutValidation(
                options.ServiceAuthorizationHeaderName,
                string.Concat("Bearer ", serviceToken));

            using var response = await httpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                return BackendResult<T>.Failure(response.StatusCode, MapBackendError(response.StatusCode));
            }

            var value = await response.Content.ReadFromJsonAsync<T>(JsonOptions, cancellationToken);
            return value is null
                ? BackendResult<T>.Failure(HttpStatusCode.BadGateway, "backend-empty-response")
                : BackendResult<T>.Success(response.StatusCode, value);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            logger.LogWarning("Timed out calling backend API for correlation {correlationId}", correlationId);
            return BackendResult<T>.Failure(HttpStatusCode.GatewayTimeout, "backend-timeout");
        }
        catch (Exception ex) when (ex is HttpRequestException or InvalidOperationException or JsonException)
        {
            logger.LogWarning(ex, "Backend API call failed for correlation {correlationId}", correlationId);
            return BackendResult<T>.Failure(HttpStatusCode.BadGateway, "backend-unavailable");
        }
    }

    private static string MapBackendError(HttpStatusCode statusCode) => statusCode switch
    {
        HttpStatusCode.Forbidden => "forbidden",
        HttpStatusCode.NotFound => "not-found",
        HttpStatusCode.Conflict => "conflict",
        HttpStatusCode.Unauthorized => "unauthorized",
        _ => "backend-error"
    };

    private static string Escape(string value) => Uri.EscapeDataString(value);
}
