using Contoso.AssetManagement.BackendApi.Authorization;
using Contoso.AssetManagement.BackendApi.Data;
using Contoso.AssetManagement.BackendApi.Observability;
using Contoso.AssetManagement.Shared;
using Contoso.AssetManagement.Shared.Auth;

namespace Contoso.AssetManagement.BackendApi;

public static class BackendHandlers
{
    public static async Task<IResult> ListPortfoliosAsync(
        string tenantId,
        HttpContext context,
        IFrontendServiceAuthenticator serviceAuthenticator,
        ITenantDirectory tenantDirectory,
        IAssetRepository repository,
        ILoggerFactory loggerFactory,
        CancellationToken cancellationToken)
    {
        const string operation = "portfolio-list";
        var logger = loggerFactory.CreateLogger("BackendApi.Portfolios");
        var authorization = await AuthorizeAsync(context, tenantId, operation, serviceAuthenticator, logger, TenantAuthorization.AuthorizeRead);
        if (authorization.Failure is not null)
        {
            return authorization.Failure;
        }

        var tenant = await ResolveTenantAsync(authorization.TokenTenantId!, tenantDirectory, context, operation, logger, cancellationToken);
        if (tenant.Failure is not null)
        {
            return tenant.Failure;
        }

        var portfolios = await repository.ListPortfoliosAsync(tenant.Entry!, cancellationToken);
        LogSuccess(logger, operation, tenant.Entry!.TenantId, context, StatusCodes.Status200OK, $"portfolio-count:{portfolios.Count}");
        return Results.Ok(portfolios);
    }

    public static async Task<IResult> GetPositionAsync(
        string tenantId,
        string portfolioId,
        string positionId,
        HttpContext context,
        IFrontendServiceAuthenticator serviceAuthenticator,
        ITenantDirectory tenantDirectory,
        IAssetRepository repository,
        ILoggerFactory loggerFactory,
        CancellationToken cancellationToken)
    {
        const string operation = "position-detail";
        var logger = loggerFactory.CreateLogger("BackendApi.Positions");
        var authorization = await AuthorizeAsync(context, tenantId, operation, serviceAuthenticator, logger, TenantAuthorization.AuthorizeRead);
        if (authorization.Failure is not null)
        {
            return authorization.Failure;
        }

        var tenant = await ResolveTenantAsync(authorization.TokenTenantId!, tenantDirectory, context, operation, logger, cancellationToken);
        if (tenant.Failure is not null)
        {
            return tenant.Failure;
        }

        var position = await repository.GetPositionAsync(tenant.Entry!, portfolioId, positionId, cancellationToken);
        if (position is null)
        {
            LogSuccess(logger, operation, tenant.Entry!.TenantId, context, StatusCodes.Status404NotFound, "not-found");
            return Results.NotFound();
        }

        if (!string.Equals(position.TenantId, tenant.Entry!.TenantId, StringComparison.Ordinal) ||
            !string.Equals(position.PortfolioId, portfolioId, StringComparison.Ordinal))
        {
            return Forbidden(logger, operation, context, TenantConstants.AuthorizationDecisions.ResourceTenantMismatch, tenant.Entry.TenantId);
        }

        LogSuccess(logger, operation, tenant.Entry.TenantId, context, StatusCodes.Status200OK, "position-returned");
        return Results.Ok(position);
    }

    public static async Task<IResult> ApproveTransactionAsync(
        string tenantId,
        string transactionId,
        HttpContext context,
        IFrontendServiceAuthenticator serviceAuthenticator,
        ITenantDirectory tenantDirectory,
        IAssetRepository repository,
        ILoggerFactory loggerFactory,
        CancellationToken cancellationToken)
    {
        const string operation = "transaction-approval";
        var logger = loggerFactory.CreateLogger("BackendApi.Transactions");
        var authorization = await AuthorizeAsync(context, tenantId, operation, serviceAuthenticator, logger, TenantAuthorization.AuthorizeApproval);
        if (authorization.Failure is not null)
        {
            return authorization.Failure;
        }

        var tenant = await ResolveTenantAsync(authorization.TokenTenantId!, tenantDirectory, context, operation, logger, cancellationToken);
        if (tenant.Failure is not null)
        {
            return tenant.Failure;
        }

        var userId = context.User.GetUserId() ?? "unknown";
        var approval = await repository.ApproveTransactionAsync(tenant.Entry!, transactionId, userId, cancellationToken);
        if (approval is null)
        {
            LogSuccess(logger, operation, tenant.Entry!.TenantId, context, StatusCodes.Status404NotFound, "not-found");
            return Results.NotFound();
        }

        if (!string.Equals(approval.TenantId, tenant.Entry!.TenantId, StringComparison.Ordinal))
        {
            return Forbidden(logger, operation, context, TenantConstants.AuthorizationDecisions.ResourceTenantMismatch, tenant.Entry.TenantId);
        }

        if (!string.Equals(approval.Status, "Approved", StringComparison.OrdinalIgnoreCase))
        {
            LogSuccess(logger, operation, tenant.Entry.TenantId, context, StatusCodes.Status409Conflict, "not-pending");
            return Results.Conflict(new { error = "transaction-not-pending" });
        }

        LogSuccess(logger, operation, tenant.Entry.TenantId, context, StatusCodes.Status200OK, "transaction-approved");
        return Results.Ok(approval);
    }

    private static async Task<(IResult? Failure, string? TokenTenantId)> AuthorizeAsync(
        HttpContext context,
        string routeTenantId,
        string operation,
        IFrontendServiceAuthenticator serviceAuthenticator,
        ILogger logger,
        Func<System.Security.Claims.ClaimsPrincipal, string, AuthorizationDecision> authorizeUser)
    {
        var serviceDecision = operation == "transaction-approval"
            ? await serviceAuthenticator.AuthenticateWriteAsync(context)
            : await serviceAuthenticator.AuthenticateReadAsync(context);
        if (!serviceDecision.Allowed)
        {
            return (Forbidden(logger, operation, context, serviceDecision.Decision, routeTenantId), null);
        }

        var userDecision = authorizeUser(context.User, routeTenantId);
        if (!userDecision.Allowed)
        {
            return (Forbidden(logger, operation, context, userDecision.Decision, userDecision.TenantId ?? routeTenantId), null);
        }

        BackendLogger.LogAuthorization(
            logger,
            LogLevel.Information,
            operation,
            userDecision.TenantId,
            context.User.GetUserId(),
            CorrelationId.Resolve(context),
            userDecision.Decision,
            StatusCodes.Status200OK);

        return (null, userDecision.TenantId);
    }

    private static async Task<(IResult? Failure, TenantDirectoryEntry? Entry)> ResolveTenantAsync(
        string tokenTenantId,
        ITenantDirectory tenantDirectory,
        HttpContext context,
        string operation,
        ILogger logger,
        CancellationToken cancellationToken)
    {
        var tenant = await tenantDirectory.GetTenantAsync(tokenTenantId, cancellationToken);
        if (tenant is null || !string.Equals(tenant.Status, TenantConstants.TenantStatus.Active, StringComparison.OrdinalIgnoreCase))
        {
            return (Forbidden(logger, operation, context, TenantConstants.AuthorizationDecisions.TenantInactive, tokenTenantId), null);
        }

        return (null, tenant);
    }

    private static IResult Forbidden(
        ILogger logger,
        string operation,
        HttpContext context,
        string authorizationDecision,
        string? tenantId)
    {
        BackendLogger.LogAuthorization(
            logger,
            LogLevel.Warning,
            operation,
            tenantId,
            context.User.GetUserId(),
            CorrelationId.Resolve(context),
            authorizationDecision,
            StatusCodes.Status403Forbidden);

        return Results.Problem(
            title: "Forbidden",
            detail: authorizationDecision,
            statusCode: StatusCodes.Status403Forbidden);
    }

    private static void LogSuccess(
        ILogger logger,
        string operation,
        string tenantId,
        HttpContext context,
        int statusCode,
        string result) =>
        BackendLogger.LogResult(
            logger,
            operation,
            tenantId,
            context.User.GetUserId(),
            CorrelationId.Resolve(context),
            result,
            statusCode);
}
