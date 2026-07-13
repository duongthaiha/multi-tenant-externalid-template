using Contoso.AssetManagement.Shared;

namespace Contoso.AssetManagement.BackendApi.Data;

public interface IAssetRepository
{
    Task<IReadOnlyList<Portfolio>> ListPortfoliosAsync(TenantDirectoryEntry tenant, CancellationToken cancellationToken);

    Task<Position?> GetPositionAsync(
        TenantDirectoryEntry tenant,
        string portfolioId,
        string positionId,
        CancellationToken cancellationToken);

    Task<TransactionApproval?> ApproveTransactionAsync(
        TenantDirectoryEntry tenant,
        string transactionId,
        string approvedBy,
        CancellationToken cancellationToken);
}
