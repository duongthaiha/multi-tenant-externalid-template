using Contoso.AssetManagement.Shared;

namespace Contoso.AssetManagement.FrontendApi.Backend;

public interface IBackendApiClient
{
    Task<BackendResult<IReadOnlyList<Portfolio>>> ListPortfoliosAsync(
        string tenantId,
        string userAccessToken,
        string correlationId,
        CancellationToken cancellationToken);

    Task<BackendResult<Position>> GetPositionAsync(
        string tenantId,
        string portfolioId,
        string positionId,
        string userAccessToken,
        string correlationId,
        CancellationToken cancellationToken);

    Task<BackendResult<TransactionApproval>> ApproveTransactionAsync(
        string tenantId,
        string transactionId,
        string userAccessToken,
        string correlationId,
        CancellationToken cancellationToken);
}
