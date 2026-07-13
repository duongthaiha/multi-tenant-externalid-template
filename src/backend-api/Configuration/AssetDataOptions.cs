namespace Contoso.AssetManagement.BackendApi.Configuration;

public sealed class AssetDataOptions
{
    public string PortfolioContainerName { get; init; } = "portfolios";
    public string PositionsContainerName { get; init; } = "positions";
    public string TransactionApprovalsContainerName { get; init; } = "transactionApprovals";
}
