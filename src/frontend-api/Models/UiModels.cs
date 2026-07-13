using Contoso.AssetManagement.Shared;

namespace Contoso.AssetManagement.FrontendApi.Models;

public sealed record PortfolioSummaryResponse(
    string TenantId,
    IReadOnlyList<PortfolioSummaryItem> Portfolios);

public sealed record PortfolioSummaryItem(
    string Id,
    string Name,
    string Currency,
    decimal MarketValue,
    DateOnly AsOfDate);

public sealed record PositionDetailResponse(
    string TenantId,
    string PortfolioId,
    PositionDetail Position);

public sealed record PositionDetail(
    string Id,
    string InstrumentName,
    string AssetClass,
    decimal Quantity,
    decimal MarketValue);

public sealed record ApprovalResponse(
    string TenantId,
    string TransactionId,
    string PortfolioId,
    string Status,
    decimal Amount,
    DateTimeOffset? ApprovedAt);

public sealed record AgentChatRequest(
    string Message,
    string? ConversationId);

public sealed record AgentChatResponse(
    string TenantId,
    string Answer,
    string CorrelationId,
    string? ConversationId,
    IReadOnlyList<AgentToolResult> ToolResults);

public sealed record AgentToolResult(
    string ToolName,
    string Result);

public static class UiModelMapper
{
    public static PortfolioSummaryResponse ToPortfolioSummary(string tenantId, IReadOnlyList<Portfolio> portfolios) =>
        new(
            tenantId,
            portfolios.Select(portfolio => new PortfolioSummaryItem(
                    portfolio.Id,
                    portfolio.Name,
                    portfolio.Currency,
                    portfolio.MarketValue,
                    portfolio.AsOfDate))
                .ToArray());

    public static PositionDetailResponse ToPositionDetail(Position position) =>
        new(
            position.TenantId,
            position.PortfolioId,
            new PositionDetail(
                position.Id,
                position.InstrumentName,
                position.AssetClass,
                position.Quantity,
                position.MarketValue));

    public static ApprovalResponse ToApproval(TransactionApproval approval) =>
        new(
            approval.TenantId,
            approval.Id,
            approval.PortfolioId,
            approval.Status,
            approval.Amount,
            approval.ApprovedAt);
}
