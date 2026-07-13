using System.Net;
using System.Text.Json;
using Azure.Core;
using Contoso.AssetManagement.FrontendApi.Agent;
using Contoso.AssetManagement.FrontendApi.Configuration;
using Contoso.AssetManagement.FrontendApi.Models;
using Contoso.AssetManagement.Shared;
using Contoso.AssetManagement.Shared.Auth;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;

namespace Contoso.AssetManagement.FrontendApi.Tests;

public sealed class FoundryPortfolioAgentClientTests
{
    [Fact]
    public async Task AskAsync_ResponsesV2UsesServerSideSessionAndDelegatedIdentity()
    {
        var handler = new CapturingHandler(new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("{\"output_text\":\"answer from agent\"}")
        });
        var client = new FoundryPortfolioAgentClient(
            new HttpClient(handler),
            new StaticTokenCredential("foundry-token"),
            Options.Create(new PortfolioAgentOptions
            {
                ResponsesEndpoint = new Uri("https://foundry.contoso.example/openai/responses?api-version=v2"),
                InvocationsEndpoint = new Uri("https://foundry.contoso.example/protocols/invocations?api-version=v1"),
                UseInvocations = true
            }),
            NullLogger<FoundryPortfolioAgentClient>.Instance);
        var delegatedIdentity = DelegatedUserIdentityFactory.FromValidatedClaims(
            CreatePrincipal("https://login.contoso.example/issuer", "user-123"),
            TenantConstants.Tenants.AlphaCapital);
        var binding = CreateBinding(delegatedIdentity);

        var result = await client.AskAsync(
            TenantConstants.Tenants.AlphaCapital,
            new AgentChatRequest("Summarize my portfolio", "client-provided-session-id"),
            binding,
            "delegated-access-value",
            "frontend-service-value",
            "corr-123",
            CancellationToken.None);

        Assert.True(result.IsSuccess);
        Assert.Equal("answer from agent", result.Value?.Answer);
        Assert.Equal(binding.SessionHandle.Value, result.Value?.ConversationId);
        Assert.NotNull(handler.Request);
        Assert.Equal(HttpMethod.Post, handler.Request.Method);
        Assert.Contains("/openai/responses", handler.Request.RequestUri?.AbsoluteUri);
        Assert.Equal("Bearer", handler.Request.Headers.Authorization?.Scheme);
        Assert.Equal("foundry-token", handler.Request.Headers.Authorization?.Parameter);
        Assert.Equal(delegatedIdentity.FoundryUserIdentity, handler.Request.Headers.GetValues(TenantConstants.Headers.FoundryUserIdentity).Single());
        AssertTrustedHeader(handler.Request, TenantConstants.Headers.AuthenticatedTenant, TenantConstants.Tenants.AlphaCapital);
        AssertTrustedHeader(handler.Request, TenantConstants.Headers.AuthenticatedUser, binding.UserId);
        AssertTrustedHeader(handler.Request, TenantConstants.Headers.UserAuthorization, "Bearer delegated-access-value");
        AssertTrustedHeader(handler.Request, TenantConstants.Headers.ServiceAuthorization, "Bearer frontend-service-value");
        AssertTrustedHeader(handler.Request, TenantConstants.Headers.CorrelationId, "corr-123");

        using var body = JsonDocument.Parse(handler.Body!);
        Assert.Equal("Summarize my portfolio", body.RootElement.GetProperty("input").GetString());
        Assert.False(body.RootElement.GetProperty("store").GetBoolean());
        Assert.Equal(binding.FoundryAgentSessionId.Value, body.RootElement.GetProperty("agent_session_id").GetString());
        Assert.Equal(binding.FoundryConversationId!.Value, body.RootElement.GetProperty("conversation").GetProperty("id").GetString());
        Assert.False(handler.Body!.Contains("client-provided-session-id", StringComparison.Ordinal));
        Assert.False(handler.Body!.Contains("delegated-access-value", StringComparison.Ordinal));
        Assert.False(handler.Body!.Contains("frontend-service-value", StringComparison.Ordinal));
        Assert.Equal(TenantConstants.Tenants.AlphaCapital, body.RootElement.GetProperty("metadata").GetProperty("contoso_tenant_id").GetString());
        Assert.Equal(binding.UserId, body.RootElement.GetProperty("metadata").GetProperty("contoso_user_id").GetString());
        Assert.Equal("corr-123", body.RootElement.GetProperty("metadata").GetProperty("contoso_correlation_id").GetString());
        Assert.False(body.RootElement.GetProperty("metadata").TryGetProperty("contoso_user_access_token", out _));
        Assert.False(body.RootElement.GetProperty("metadata").TryGetProperty("contoso_service_token", out _));
    }

    private static void AssertTrustedHeader(HttpRequestMessage request, string headerName, string expectedValue)
    {
        Assert.Equal(expectedValue, request.Headers.GetValues(headerName).Single());
        Assert.Equal(expectedValue, request.Headers.GetValues(TenantConstants.Headers.ClientForwarded(headerName)).Single());
    }

    private static AgentSessionBinding CreateBinding(DelegatedUserIdentity delegatedIdentity) => new(
        new AgentSessionHandle("app-session-1"),
        new FoundryAgentSessionId("server-foundry-session-1"),
        TenantConstants.Tenants.AlphaCapital,
        delegatedIdentity.AppUserId,
        new FoundryUserIdentity(delegatedIdentity.FoundryUserIdentity),
        "portfolio-agent",
        AgentSessionProtocolMode.ResponsesV2Delegated,
        DateTimeOffset.UtcNow.AddMinutes(-1),
        DateTimeOffset.UtcNow.AddMinutes(-1),
        DateTimeOffset.UtcNow.AddHours(1),
        AgentSessionStatus.Active,
        FoundryConversationId: new FoundryConversationId("foundry-conversation-1"));

    private static ClaimsPrincipal CreatePrincipal(string issuer, string objectId)
    {
        var claims = new[]
        {
            new Claim(TenantConstants.Claims.Issuer, issuer),
            new Claim(TenantConstants.Claims.ObjectId, objectId)
        };
        return new ClaimsPrincipal(new ClaimsIdentity(claims, authenticationType: "Test"));
    }

    private sealed class CapturingHandler(HttpResponseMessage response) : HttpMessageHandler
    {
        public HttpRequestMessage? Request { get; private set; }
        public string? Body { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Request = request;
            Body = request.Content is null ? null : await request.Content.ReadAsStringAsync(cancellationToken);
            return response;
        }
    }

    private sealed class StaticTokenCredential(string token) : TokenCredential
    {
        public override AccessToken GetToken(TokenRequestContext requestContext, CancellationToken cancellationToken) =>
            new(token, DateTimeOffset.UtcNow.AddHours(1));

        public override ValueTask<AccessToken> GetTokenAsync(TokenRequestContext requestContext, CancellationToken cancellationToken) =>
            ValueTask.FromResult(new AccessToken(token, DateTimeOffset.UtcNow.AddHours(1)));
    }
}
