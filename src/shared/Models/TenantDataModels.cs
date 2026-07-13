namespace Contoso.AssetManagement.Shared;

public sealed record Portfolio(
    string Id,
    string TenantId,
    string Name,
    string Currency,
    decimal MarketValue,
    DateOnly AsOfDate);

public sealed record Position(
    string Id,
    string TenantId,
    string PortfolioId,
    string InstrumentName,
    string AssetClass,
    decimal Quantity,
    decimal MarketValue);

public sealed record TransactionApproval(
    string Id,
    string TenantId,
    string PortfolioId,
    string RequestedBy,
    string Status,
    decimal Amount,
    DateTimeOffset CreatedAt,
    string? ApprovedBy,
    DateTimeOffset? ApprovedAt);
