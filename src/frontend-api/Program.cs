using Azure.Core;
using Azure.Identity;
using Contoso.AssetManagement.FrontendApi.Agent;
using Contoso.AssetManagement.FrontendApi;
using Contoso.AssetManagement.FrontendApi.Backend;
using Contoso.AssetManagement.FrontendApi.Configuration;
using Contoso.AssetManagement.Shared;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.Tokens;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddOptions<AuthOptions>().BindConfiguration("Auth");
builder.Services.AddOptions<BackendApiOptions>().BindConfiguration("BackendApi");
builder.Services.AddOptions<PortfolioAgentOptions>().BindConfiguration("PortfolioAgent");
builder.Services.AddOptions<AgentSessionBindingStoreOptions>().BindConfiguration("AgentSessionBindingStore");
builder.Services.AddApplicationInsightsTelemetry();

var authOptions = builder.Configuration.GetSection("Auth").Get<AuthOptions>() ?? new AuthOptions();

builder.Services
    .AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
    .AddJwtBearer(options => ConfigureBearer(options, authOptions));

builder.Services.AddAuthorization();
builder.Services.AddSingleton<TokenCredential, DefaultAzureCredential>();
builder.Services.AddSingleton<IBackendServiceTokenProvider, ManagedIdentityBackendServiceTokenProvider>();
builder.Services.AddSingleton<IAgentSessionBindingStore>(serviceProvider =>
{
    var options = serviceProvider.GetRequiredService<IOptions<AgentSessionBindingStoreOptions>>().Value;
    return options.UseInMemory
        ? new InMemoryAgentSessionBindingStore()
        : new CosmosAgentSessionBindingStore(
            serviceProvider.GetRequiredService<IOptions<AgentSessionBindingStoreOptions>>(),
            serviceProvider.GetRequiredService<TokenCredential>());
});
builder.Services.AddHttpClient<IAgentChatClient, FoundryPortfolioAgentClient>((serviceProvider, client) =>
{
    var agentOptions = serviceProvider.GetRequiredService<IOptions<PortfolioAgentOptions>>().Value;
    client.Timeout = agentOptions.Timeout;
});
builder.Services.AddHttpClient<IFoundrySessionClient, FoundrySessionClient>((serviceProvider, client) =>
{
    var agentOptions = serviceProvider.GetRequiredService<IOptions<PortfolioAgentOptions>>().Value;
    client.Timeout = agentOptions.Timeout;
});
builder.Services.AddHttpClient<IFoundryConversationClient, FoundryConversationClient>((serviceProvider, client) =>
{
    var agentOptions = serviceProvider.GetRequiredService<IOptions<PortfolioAgentOptions>>().Value;
    client.Timeout = agentOptions.Timeout;
});
builder.Services.AddHttpClient<IBackendApiClient, BackendApiClient>((serviceProvider, client) =>
{
    var backendOptions = serviceProvider.GetRequiredService<IOptions<BackendApiOptions>>().Value;
    client.BaseAddress = backendOptions.BaseAddress;
    client.Timeout = backendOptions.Timeout;
});

var app = builder.Build();

app.UseAuthentication();
app.UseAuthorization();

app.MapGet("/health", () => Results.Ok(new { status = "healthy" }));

app.MapGet("/api/tenants/{tenantId}/portfolios", FrontendHandlers.ListPortfoliosAsync)
    .RequireAuthorization();

app.MapGet("/api/tenants/{tenantId}/portfolios/{portfolioId}/positions/{positionId}", FrontendHandlers.GetPositionAsync)
    .RequireAuthorization();

app.MapPost("/api/tenants/{tenantId}/transactions/{transactionId}/approve", FrontendHandlers.ApproveTransactionAsync)
    .RequireAuthorization();

app.MapPost("/api/tenants/{tenantId}/agent/chat", FrontendHandlers.ChatWithPortfolioAgentAsync)
    .RequireAuthorization();

app.MapDelete("/api/tenants/{tenantId}/agent/sessions/{sessionHandle}", FrontendHandlers.DeletePortfolioAgentSessionAsync)
    .RequireAuthorization();

app.Run();

static void ConfigureBearer(JwtBearerOptions options, AuthOptions authOptions)
{
    options.MapInboundClaims = false;
    options.IncludeErrorDetails = false;
    options.SaveToken = false;

    if (!string.IsNullOrWhiteSpace(authOptions.MetadataAddress))
    {
        options.MetadataAddress = authOptions.MetadataAddress;
    }
    else if (!string.IsNullOrWhiteSpace(authOptions.Authority))
    {
        options.Authority = authOptions.Authority;
    }
    else if (!string.IsNullOrWhiteSpace(authOptions.Issuer))
    {
        options.Authority = authOptions.Issuer;
    }

    options.TokenValidationParameters = new TokenValidationParameters
    {
        ValidateIssuer = true,
        ValidIssuers = new[] { authOptions.Issuer }.Concat(authOptions.AdditionalIssuers).Where(issuer => !string.IsNullOrWhiteSpace(issuer)),
        ValidateAudience = true,
        ValidAudiences = new[] { authOptions.Audience }.Concat(authOptions.AdditionalAudiences).Where(audience => !string.IsNullOrWhiteSpace(audience)),
        ValidateLifetime = true,
        ValidateIssuerSigningKey = true,
        RequireExpirationTime = true,
        RequireSignedTokens = true,
        NameClaimType = TenantConstants.Claims.PreferredUsername,
        RoleClaimType = TenantConstants.Claims.Roles,
        ClockSkew = TimeSpan.FromMinutes(2)
    };
}
