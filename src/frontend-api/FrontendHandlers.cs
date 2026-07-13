using System.Net;
using System.Security.Cryptography;
using System.Text;
using Contoso.AssetManagement.FrontendApi.Agent;
using Contoso.AssetManagement.FrontendApi.Backend;
using Contoso.AssetManagement.FrontendApi.Configuration;
using Contoso.AssetManagement.FrontendApi.Models;
using Contoso.AssetManagement.FrontendApi.Observability;
using Contoso.AssetManagement.Shared;
using Contoso.AssetManagement.Shared.Auth;
using Microsoft.Extensions.Options;
using Microsoft.Net.Http.Headers;

namespace Contoso.AssetManagement.FrontendApi;

public static class FrontendHandlers
{
    public static async Task<IResult> ListPortfoliosAsync(
        string tenantId,
        HttpContext context,
        IBackendApiClient backendClient,
        ILoggerFactory loggerFactory,
        CancellationToken cancellationToken)
    {
        const string operation = "portfolio-list";
        var logger = loggerFactory.CreateLogger("FrontendApi.Portfolios");
        var authorization = Authorize(context, tenantId, operation, logger, TenantAuthorization.AuthorizeRead);
        if (authorization.Failure is not null)
        {
            return authorization.Failure;
        }

        var result = await backendClient.ListPortfoliosAsync(tenantId, authorization.UserAccessToken!, authorization.CorrelationId, cancellationToken);
        if (!result.IsSuccess || result.Value is null)
        {
            return BackendFailure(result, context, tenantId, operation, logger, authorization.CorrelationId);
        }

        var response = UiModelMapper.ToPortfolioSummary(tenantId, result.Value);
        LogSuccess(logger, operation, tenantId, context, authorization.CorrelationId, StatusCodes.Status200OK, $"portfolio-count:{response.Portfolios.Count}");
        return Results.Ok(response);
    }

    public static async Task<IResult> GetPositionAsync(
        string tenantId,
        string portfolioId,
        string positionId,
        HttpContext context,
        IBackendApiClient backendClient,
        ILoggerFactory loggerFactory,
        CancellationToken cancellationToken)
    {
        const string operation = "position-detail";
        var logger = loggerFactory.CreateLogger("FrontendApi.Positions");
        var authorization = Authorize(context, tenantId, operation, logger, TenantAuthorization.AuthorizeRead);
        if (authorization.Failure is not null)
        {
            return authorization.Failure;
        }

        var result = await backendClient.GetPositionAsync(tenantId, portfolioId, positionId, authorization.UserAccessToken!, authorization.CorrelationId, cancellationToken);
        if (!result.IsSuccess || result.Value is null)
        {
            return BackendFailure(result, context, tenantId, operation, logger, authorization.CorrelationId);
        }

        var response = UiModelMapper.ToPositionDetail(result.Value);
        LogSuccess(logger, operation, tenantId, context, authorization.CorrelationId, StatusCodes.Status200OK, "position-returned");
        return Results.Ok(response);
    }

    public static async Task<IResult> ApproveTransactionAsync(
        string tenantId,
        string transactionId,
        HttpContext context,
        IBackendApiClient backendClient,
        ILoggerFactory loggerFactory,
        CancellationToken cancellationToken)
    {
        const string operation = "transaction-approval";
        var logger = loggerFactory.CreateLogger("FrontendApi.Transactions");
        var authorization = Authorize(context, tenantId, operation, logger, TenantAuthorization.AuthorizeApproval);
        if (authorization.Failure is not null)
        {
            return authorization.Failure;
        }

        var result = await backendClient.ApproveTransactionAsync(tenantId, transactionId, authorization.UserAccessToken!, authorization.CorrelationId, cancellationToken);
        if (!result.IsSuccess || result.Value is null)
        {
            return BackendFailure(result, context, tenantId, operation, logger, authorization.CorrelationId);
        }

        var response = UiModelMapper.ToApproval(result.Value);
        LogSuccess(logger, operation, tenantId, context, authorization.CorrelationId, StatusCodes.Status200OK, "transaction-approved");
        return Results.Ok(response);
    }

    public static async Task<IResult> ChatWithPortfolioAgentAsync(
        string tenantId,
        AgentChatRequest request,
        HttpContext context,
        IAgentChatClient agentClient,
        IAgentSessionBindingStore sessionBindingStore,
        IFoundrySessionClient foundrySessionClient,
        IFoundryConversationClient foundryConversationClient,
        IBackendServiceTokenProvider serviceTokenProvider,
        IOptions<PortfolioAgentOptions> agentOptions,
        ILoggerFactory loggerFactory,
        CancellationToken cancellationToken)
    {
        const string operation = "portfolio-agent-chat";
        var logger = loggerFactory.CreateLogger("FrontendApi.PortfolioAgent");
        if (string.IsNullOrWhiteSpace(request.Message))
        {
            return Results.Problem(title: "Invalid chat request", detail: "message-required", statusCode: StatusCodes.Status400BadRequest);
        }

        var authorization = Authorize(context, tenantId, operation, logger, TenantAuthorization.AuthorizeRead);
        if (authorization.Failure is not null)
        {
            return authorization.Failure;
        }

        DelegatedUserIdentity delegatedIdentity;
        try
        {
            delegatedIdentity = DelegatedUserIdentityFactory.FromValidatedClaims(context.User, tenantId);
        }
        catch (Exception ex) when (ex is ArgumentException or InvalidOperationException)
        {
            FrontendLogger.LogAuthorization(
                logger,
                LogLevel.Warning,
                operation,
                tenantId,
                context.User.GetUserId(),
                authorization.CorrelationId,
                "invalid-delegated-user-identity",
                StatusCodes.Status403Forbidden);
            return Results.Problem(title: "Forbidden", detail: "invalid-delegated-user-identity", statusCode: StatusCodes.Status403Forbidden);
        }

        var bindingResult = await ResolvePortfolioAgentSessionBindingAsync(
            request.ConversationId,
            tenantId,
            delegatedIdentity,
            sessionBindingStore,
            foundrySessionClient,
            foundryConversationClient,
            agentOptions.Value,
            logger,
            authorization.CorrelationId,
            cancellationToken);
        if (!bindingResult.IsSuccess || bindingResult.Binding is null)
        {
            FrontendLogger.LogAuthorization(
                logger,
                LogLevel.Warning,
                operation,
                tenantId,
                delegatedIdentity.AppUserId,
                authorization.CorrelationId,
                bindingResult.Error ?? "foundry-session-binding-error",
                bindingResult.StatusCode);
            return bindingResult.StatusCode switch
            {
                StatusCodes.Status404NotFound => Results.NotFound(),
                StatusCodes.Status403Forbidden => Results.Problem(title: "Forbidden", detail: "forbidden", statusCode: StatusCodes.Status403Forbidden),
                StatusCodes.Status504GatewayTimeout => Results.Problem(title: "Portfolio agent timeout", detail: "portfolio-agent-timeout", statusCode: StatusCodes.Status504GatewayTimeout),
                _ => Results.Problem(title: "Portfolio agent unavailable", detail: bindingResult.Error ?? "portfolio-agent-unavailable", statusCode: StatusCodes.Status502BadGateway)
            };
        }

        var serviceToken = await serviceTokenProvider.GetServiceTokenAsync(cancellationToken);
        var result = await agentClient.AskAsync(
            tenantId,
            request,
            bindingResult.Binding,
            authorization.UserAccessToken!,
            serviceToken,
            authorization.CorrelationId,
            cancellationToken);
        if (!result.IsSuccess || result.Value is null)
        {
            FrontendLogger.LogResult(logger, operation, tenantId, delegatedIdentity.AppUserId, authorization.CorrelationId, result.Error ?? "portfolio-agent-error", (int)result.StatusCode);
            return result.StatusCode switch
            {
                HttpStatusCode.Forbidden => Results.Problem(title: "Forbidden", detail: "forbidden", statusCode: StatusCodes.Status403Forbidden),
                HttpStatusCode.Unauthorized => Results.Problem(title: "Unauthorized", detail: "portfolio-agent-authentication-failed", statusCode: StatusCodes.Status502BadGateway),
                HttpStatusCode.GatewayTimeout => Results.Problem(title: "Portfolio agent timeout", detail: "portfolio-agent-timeout", statusCode: StatusCodes.Status504GatewayTimeout),
                _ => Results.Problem(title: "Portfolio agent unavailable", detail: result.Error ?? "portfolio-agent-unavailable", statusCode: StatusCodes.Status502BadGateway)
            };
        }

        await sessionBindingStore.MarkUsedAsync(
            bindingResult.Binding,
            DateTimeOffset.UtcNow,
            DateTimeOffset.UtcNow.Add(agentOptions.Value.SessionTtl),
            authorization.CorrelationId,
            cancellationToken);
        LogSuccess(logger, operation, tenantId, context, authorization.CorrelationId, StatusCodes.Status200OK, "agent-answer-returned");
        return Results.Ok(result.Value);
    }

    public static async Task<IResult> DeletePortfolioAgentSessionAsync(
        string tenantId,
        string sessionHandle,
        HttpContext context,
        IAgentSessionBindingStore sessionBindingStore,
        IFoundrySessionClient foundrySessionClient,
        IOptions<PortfolioAgentOptions> agentOptions,
        ILoggerFactory loggerFactory,
        CancellationToken cancellationToken)
    {
        const string operation = "portfolio-agent-session-cleanup";
        var logger = loggerFactory.CreateLogger("FrontendApi.PortfolioAgent");
        var authorization = Authorize(context, tenantId, operation, logger, TenantAuthorization.AuthorizeRead);
        if (authorization.Failure is not null)
        {
            return authorization.Failure;
        }

        DelegatedUserIdentity delegatedIdentity;
        try
        {
            delegatedIdentity = DelegatedUserIdentityFactory.FromValidatedClaims(context.User, tenantId);
        }
        catch (Exception ex) when (ex is ArgumentException or InvalidOperationException)
        {
            FrontendLogger.LogAuthorization(
                logger,
                LogLevel.Warning,
                operation,
                tenantId,
                context.User.GetUserId(),
                authorization.CorrelationId,
                "invalid-delegated-user-identity",
                StatusCodes.Status403Forbidden);
            return Results.Problem(title: "Forbidden", detail: "invalid-delegated-user-identity", statusCode: StatusCodes.Status403Forbidden);
        }

        if (!TryCreateSessionHandle(sessionHandle, out var ownedSessionHandle))
        {
            LogSessionDecision(logger, "foundry-session-owner-mismatch", tenantId, delegatedIdentity.AppUserId, authorization.CorrelationId, sessionHandle, StatusCodes.Status404NotFound);
            return Results.NotFound();
        }

        var binding = await sessionBindingStore.GetOwnedAsync(
            ownedSessionHandle,
            tenantId,
            delegatedIdentity.AppUserId,
            agentOptions.Value.AgentName,
            cancellationToken);
        if (binding is null)
        {
            LogSessionDecision(logger, "foundry-session-owner-mismatch", tenantId, delegatedIdentity.AppUserId, authorization.CorrelationId, ownedSessionHandle.Value, StatusCodes.Status404NotFound);
            return Results.NotFound();
        }

        if (binding.Status is AgentSessionStatus.Deleted or AgentSessionStatus.Expired or AgentSessionStatus.Replaced)
        {
            LogSessionDecision(logger, "foundry-session-already-cleaned", tenantId, delegatedIdentity.AppUserId, authorization.CorrelationId, ownedSessionHandle.Value, StatusCodes.Status204NoContent);
            return Results.NoContent();
        }

        var foundryIdentity = new FoundryUserIdentity(delegatedIdentity.FoundryUserIdentity);
        var foundrySession = await foundrySessionClient.DeleteAsync(binding.FoundryAgentSessionId, foundryIdentity, authorization.CorrelationId, cancellationToken);
        if (foundrySession.IsSuccess || foundrySession.Error?.Kind is FoundrySessionErrorKind.NotFound or FoundrySessionErrorKind.ExpiredNotRunning)
        {
            await sessionBindingStore.MarkDeletedAsync(binding, DateTimeOffset.UtcNow, authorization.CorrelationId, cancellationToken);
            LogSessionDecision(logger, "foundry-session-delete", tenantId, delegatedIdentity.AppUserId, authorization.CorrelationId, ownedSessionHandle.Value, StatusCodes.Status204NoContent);
            return Results.NoContent();
        }

        var statusCode = MapFoundrySessionStatusCode(foundrySession.Error);
        FrontendLogger.LogResult(
            logger,
            operation,
            tenantId,
            delegatedIdentity.AppUserId,
            authorization.CorrelationId,
            foundrySession.Error?.Code ?? "foundry-session-delete-failed",
            statusCode);
        return statusCode switch
        {
            StatusCodes.Status403Forbidden => Results.Problem(title: "Forbidden", detail: "forbidden", statusCode: StatusCodes.Status403Forbidden),
            StatusCodes.Status504GatewayTimeout => Results.Problem(title: "Portfolio agent timeout", detail: "portfolio-agent-timeout", statusCode: StatusCodes.Status504GatewayTimeout),
            _ => Results.Problem(title: "Portfolio agent unavailable", detail: "portfolio-agent-unavailable", statusCode: StatusCodes.Status502BadGateway)
        };
    }

    private static async Task<SessionBindingResolution> ResolvePortfolioAgentSessionBindingAsync(
        string? requestedHandle,
        string tenantId,
        DelegatedUserIdentity delegatedIdentity,
        IAgentSessionBindingStore bindingStore,
        IFoundrySessionClient foundrySessionClient,
        IFoundryConversationClient foundryConversationClient,
        PortfolioAgentOptions options,
        ILogger logger,
        string correlationId,
        CancellationToken cancellationToken)
    {
        var foundryIdentity = new FoundryUserIdentity(delegatedIdentity.FoundryUserIdentity);
        if (!TryCreateSessionHandle(requestedHandle, out var sessionHandle))
        {
            return await CreateBindingAsync(tenantId, delegatedIdentity.AppUserId, foundryIdentity, bindingStore, foundrySessionClient, foundryConversationClient, options, logger, correlationId, cancellationToken);
        }

        var binding = await bindingStore.GetOwnedAsync(sessionHandle, tenantId, delegatedIdentity.AppUserId, options.AgentName, cancellationToken);
        if (binding is null || binding.Status != AgentSessionStatus.Active || binding.ExpiresAt <= DateTimeOffset.UtcNow)
        {
            LogSessionDecision(logger, "foundry-session-owner-mismatch", tenantId, delegatedIdentity.AppUserId, correlationId, sessionHandle.Value, StatusCodes.Status404NotFound);
            return SessionBindingResolution.Failure(StatusCodes.Status404NotFound, "foundry-session-owner-mismatch");
        }

        if (!options.ValidateSessionBeforeInvoke)
        {
            var conversationBinding = await EnsureConversationBindingAsync(binding, foundryIdentity, bindingStore, foundryConversationClient, options, logger, correlationId, cancellationToken);
            if (!conversationBinding.IsSuccess)
            {
                return conversationBinding;
            }

            binding = conversationBinding.Binding!;
            LogSessionDecision(logger, "foundry-session-resume", tenantId, delegatedIdentity.AppUserId, correlationId, binding.SessionHandle.Value, StatusCodes.Status200OK);
            return SessionBindingResolution.Success(binding);
        }

        var foundrySession = await foundrySessionClient.GetAsync(binding.FoundryAgentSessionId, foundryIdentity, correlationId, cancellationToken);
        if (foundrySession.IsSuccess)
        {
            var conversationBinding = await EnsureConversationBindingAsync(binding, foundryIdentity, bindingStore, foundryConversationClient, options, logger, correlationId, cancellationToken);
            if (!conversationBinding.IsSuccess)
            {
                return conversationBinding;
            }

            binding = conversationBinding.Binding!;
            LogSessionDecision(logger, "foundry-session-resume", tenantId, delegatedIdentity.AppUserId, correlationId, binding.SessionHandle.Value, StatusCodes.Status200OK);
            return SessionBindingResolution.Success(binding);
        }

        if (foundrySession.Error?.Kind is FoundrySessionErrorKind.NotFound or FoundrySessionErrorKind.ExpiredNotRunning)
        {
            var replacement = await CreateBindingAsync(tenantId, delegatedIdentity.AppUserId, foundryIdentity, bindingStore, foundrySessionClient, foundryConversationClient, options, logger, correlationId, cancellationToken);
            if (replacement.IsSuccess && replacement.Binding is not null)
            {
                await bindingStore.MarkReplacedAsync(binding, replacement.Binding.SessionHandle, DateTimeOffset.UtcNow, correlationId, cancellationToken);
            }
            else
            {
                await bindingStore.MarkExpiredAsync(binding, DateTimeOffset.UtcNow, correlationId, cancellationToken);
            }

            return replacement;
        }

        return SessionBindingResolution.Failure(MapFoundrySessionStatusCode(foundrySession.Error), foundrySession.Error?.Code ?? "foundry-session-get-failed");
    }

    private static async Task<SessionBindingResolution> CreateBindingAsync(
        string tenantId,
        string userId,
        FoundryUserIdentity foundryIdentity,
        IAgentSessionBindingStore bindingStore,
        IFoundrySessionClient foundrySessionClient,
        IFoundryConversationClient foundryConversationClient,
        PortfolioAgentOptions options,
        ILogger logger,
        string correlationId,
        CancellationToken cancellationToken)
    {
        var foundrySession = await foundrySessionClient.CreateAsync(foundryIdentity, correlationId, cancellationToken);
        if (!foundrySession.IsSuccess || foundrySession.Session is null)
        {
            return SessionBindingResolution.Failure(MapFoundrySessionStatusCode(foundrySession.Error), foundrySession.Error?.Code ?? "foundry-session-create-failed");
        }

        var foundryConversation = await foundryConversationClient.CreateAsync(foundryIdentity, correlationId, cancellationToken);
        if (!foundryConversation.IsSuccess || foundryConversation.ConversationId is null)
        {
            return SessionBindingResolution.Failure(MapFoundrySessionStatusCode(foundryConversation.Error), foundryConversation.Error?.Code ?? "foundry-conversation-create-failed");
        }

        var now = DateTimeOffset.UtcNow;
        var expiresAt = Min(foundrySession.Session.ExpiresAt ?? now.Add(options.SessionTtl), now.Add(options.SessionTtl));
        var binding = await bindingStore.CreateAsync(
            new AgentSessionBinding(
                NewSessionHandle(),
                foundrySession.Session.AgentSessionId,
                tenantId,
                userId,
                foundryIdentity,
                options.AgentName,
                AgentSessionProtocolMode.ResponsesV2Delegated,
                now,
                now,
                expiresAt,
                AgentSessionStatus.Active,
                CorrelationId: correlationId,
                FoundryConversationId: foundryConversation.ConversationId),
            cancellationToken);

        LogSessionDecision(logger, "foundry-session-create", tenantId, userId, correlationId, binding.SessionHandle.Value, StatusCodes.Status201Created);
        return SessionBindingResolution.Success(binding);
    }

    private static async Task<SessionBindingResolution> EnsureConversationBindingAsync(
        AgentSessionBinding binding,
        FoundryUserIdentity foundryIdentity,
        IAgentSessionBindingStore bindingStore,
        IFoundryConversationClient foundryConversationClient,
        PortfolioAgentOptions options,
        ILogger logger,
        string correlationId,
        CancellationToken cancellationToken)
    {
        if (binding.FoundryConversationId is not null)
        {
            return SessionBindingResolution.Success(binding);
        }

        var foundryConversation = await foundryConversationClient.CreateAsync(foundryIdentity, correlationId, cancellationToken);
        if (!foundryConversation.IsSuccess || foundryConversation.ConversationId is null)
        {
            return SessionBindingResolution.Failure(MapFoundrySessionStatusCode(foundryConversation.Error), foundryConversation.Error?.Code ?? "foundry-conversation-create-failed");
        }

        var now = DateTimeOffset.UtcNow;
        var updated = await bindingStore.MarkUsedAsync(
            binding with { FoundryConversationId = foundryConversation.ConversationId },
            now,
            Min(binding.ExpiresAt, now.Add(options.SessionTtl)),
            correlationId,
            cancellationToken);

        LogSessionDecision(logger, "foundry-conversation-create", binding.TenantId, binding.UserId, correlationId, updated.SessionHandle.Value, StatusCodes.Status201Created);
        return SessionBindingResolution.Success(updated);
    }

    private static bool TryCreateSessionHandle(string? value, out AgentSessionHandle sessionHandle)
    {
        sessionHandle = new AgentSessionHandle("unused");
        if (string.IsNullOrWhiteSpace(value) || value.Length > 128 || !value.All(IsSessionHandleCharacter))
        {
            return false;
        }

        sessionHandle = new AgentSessionHandle(value);
        return true;
    }

    private static bool IsSessionHandleCharacter(char character) =>
        char.IsAsciiLetterOrDigit(character) || character is '-' or '_';

    private static AgentSessionHandle NewSessionHandle() =>
        new($"agsh_{Guid.NewGuid():N}");

    private static int MapFoundrySessionStatusCode(FoundrySessionError? error) =>
        error?.Kind switch
        {
            FoundrySessionErrorKind.ForbiddenNotAccessible => StatusCodes.Status403Forbidden,
            FoundrySessionErrorKind.Transient when error.StatusCode == StatusCodes.Status504GatewayTimeout => StatusCodes.Status504GatewayTimeout,
            FoundrySessionErrorKind.NotFound => StatusCodes.Status404NotFound,
            _ => StatusCodes.Status502BadGateway
        };

    private static DateTimeOffset Min(DateTimeOffset left, DateTimeOffset right) =>
        left <= right ? left : right;

    private static void LogSessionDecision(
        ILogger logger,
        string result,
        string tenantId,
        string userId,
        string correlationId,
        string sessionHandle,
        int statusCode) =>
        logger.LogInformation(
            "Frontend API Foundry session decision {Result} for tenant {TenantId}, user {UserId}, correlation {CorrelationId}, sessionHandleHash {SessionHandleHash}, status {StatusCode}.",
            result,
            tenantId,
            userId,
            correlationId,
            HashForLog(sessionHandle),
            statusCode);

    private static string HashForLog(string value)
    {
        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(value));
        return Convert.ToHexString(hash)[..16].ToLowerInvariant();
    }

    private sealed record SessionBindingResolution(bool IsSuccess, AgentSessionBinding? Binding, int StatusCode, string? Error)
    {
        public static SessionBindingResolution Success(AgentSessionBinding binding) =>
            new(true, binding, StatusCodes.Status200OK, null);

        public static SessionBindingResolution Failure(int statusCode, string error) =>
            new(false, null, statusCode, error);
    }

    private static (IResult? Failure, string? UserAccessToken, string CorrelationId) Authorize(
        HttpContext context,
        string routeTenantId,
        string operation,
        ILogger logger,
        Func<System.Security.Claims.ClaimsPrincipal, string, AuthorizationDecision> authorizeUser)
    {
        var correlationId = CorrelationId.Resolve(context);
        context.Response.Headers[CorrelationId.HeaderName] = correlationId;

        var userDecision = authorizeUser(context.User, routeTenantId);
        if (!userDecision.Allowed)
        {
            FrontendLogger.LogAuthorization(
                logger,
                LogLevel.Warning,
                operation,
                userDecision.TenantId ?? routeTenantId,
                context.User.GetUserId(),
                correlationId,
                userDecision.Decision,
                StatusCodes.Status403Forbidden);

            return (Results.Problem(
                title: "Forbidden",
                detail: userDecision.Decision,
                statusCode: StatusCodes.Status403Forbidden), null, correlationId);
        }

        var userAccessToken = ExtractBearerToken(context);
        if (string.IsNullOrWhiteSpace(userAccessToken))
        {
            FrontendLogger.LogAuthorization(
                logger,
                LogLevel.Warning,
                operation,
                userDecision.TenantId,
                context.User.GetUserId(),
                correlationId,
                "missing-user-token",
                StatusCodes.Status401Unauthorized);

            return (Results.Unauthorized(), null, correlationId);
        }

        FrontendLogger.LogAuthorization(
            logger,
            LogLevel.Information,
            operation,
            userDecision.TenantId,
            context.User.GetUserId(),
            correlationId,
            userDecision.Decision,
            StatusCodes.Status200OK);

        return (null, userAccessToken, correlationId);
    }

    private static IResult BackendFailure<T>(
        BackendResult<T> result,
        HttpContext context,
        string tenantId,
        string operation,
        ILogger logger,
        string correlationId)
    {
        var statusCode = (int)result.StatusCode;
        FrontendLogger.LogResult(logger, operation, tenantId, context.User.GetUserId(), correlationId, result.Error ?? "backend-error", statusCode);

        return result.StatusCode switch
        {
            HttpStatusCode.NotFound => Results.NotFound(),
            HttpStatusCode.Forbidden => Results.Problem(title: "Forbidden", detail: "forbidden", statusCode: StatusCodes.Status403Forbidden),
            HttpStatusCode.Unauthorized => Results.Problem(title: "Unauthorized", detail: "backend-authentication-failed", statusCode: StatusCodes.Status502BadGateway),
            HttpStatusCode.Conflict => Results.Conflict(new { error = "transaction-not-pending" }),
            HttpStatusCode.GatewayTimeout => Results.Problem(title: "Backend timeout", detail: "backend-timeout", statusCode: StatusCodes.Status504GatewayTimeout),
            _ => Results.Problem(title: "Backend unavailable", detail: "backend-unavailable", statusCode: StatusCodes.Status502BadGateway)
        };
    }

    private static void LogSuccess(
        ILogger logger,
        string operation,
        string tenantId,
        HttpContext context,
        string correlationId,
        int statusCode,
        string result) =>
        FrontendLogger.LogResult(
            logger,
            operation,
            tenantId,
            context.User.GetUserId(),
            correlationId,
            result,
            statusCode);

    private static string? ExtractBearerToken(HttpContext context)
    {
        var authorization = context.Request.Headers[HeaderNames.Authorization].FirstOrDefault();
        if (string.IsNullOrWhiteSpace(authorization) || !authorization.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
        {
            return null;
        }

        return authorization["Bearer ".Length..].Trim();
    }
}
