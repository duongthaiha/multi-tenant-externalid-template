using System.Text.Json;
using Azure.Core;
using Azure.Identity;
using Contoso.AssetManagement.BackendApi;
using Contoso.AssetManagement.BackendApi.Authorization;
using Contoso.AssetManagement.BackendApi.Configuration;
using Contoso.AssetManagement.BackendApi.Data;
using Contoso.AssetManagement.BackendApi.Infrastructure;
using Contoso.AssetManagement.Shared;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.IdentityModel.Tokens;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddOptions<AuthOptions>().BindConfiguration("Auth");
builder.Services.AddOptions<ServiceAuthOptions>().BindConfiguration("ServiceAuth");
builder.Services.AddOptions<ControlPlaneOptions>().BindConfiguration("ControlPlane");
builder.Services.AddOptions<AssetDataOptions>().BindConfiguration("AssetData");
builder.Services.AddApplicationInsightsTelemetry();

var authOptions = builder.Configuration.GetSection("Auth").Get<AuthOptions>() ?? new AuthOptions();
var serviceAuthOptions = builder.Configuration.GetSection("ServiceAuth").Get<ServiceAuthOptions>() ?? new ServiceAuthOptions();

builder.Services
    .AddAuthentication(options =>
    {
        options.DefaultAuthenticateScheme = AuthenticationSchemes.UserBearer;
        options.DefaultChallengeScheme = AuthenticationSchemes.UserBearer;
    })
    .AddJwtBearer(AuthenticationSchemes.UserBearer, options => ConfigureBearer(options, authOptions))
    .AddJwtBearer(AuthenticationSchemes.ServiceBearer, options =>
    {
        var issuer = string.IsNullOrWhiteSpace(serviceAuthOptions.Issuer) ? authOptions.Issuer : serviceAuthOptions.Issuer;
        var audience = string.IsNullOrWhiteSpace(serviceAuthOptions.Audience) ? authOptions.Audience : serviceAuthOptions.Audience;
        ConfigureBearer(options, new AuthOptions
        {
            Authority = serviceAuthOptions.Authority ?? authOptions.Authority,
            MetadataAddress = serviceAuthOptions.MetadataAddress ?? authOptions.MetadataAddress,
            Issuer = issuer ?? string.Empty,
            AdditionalIssuers = serviceAuthOptions.AdditionalIssuers,
            Audience = audience ?? string.Empty
        });
        options.Events = new JwtBearerEvents
        {
            OnMessageReceived = context =>
            {
                var logger = context.HttpContext.RequestServices.GetRequiredService<ILoggerFactory>().CreateLogger("BackendApi.ServiceAuth");
                if (context.Request.Headers.TryGetValue(serviceAuthOptions.HeaderName, out var values))
                {
                    var header = values.FirstOrDefault();
                    if (!string.IsNullOrWhiteSpace(header) && header.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
                    {
                        context.Token = header["Bearer ".Length..].Trim();
                        logger.LogInformation("Service authentication header {HeaderName} received.", serviceAuthOptions.HeaderName);
                    }
                    else
                    {
                        logger.LogWarning("Service authentication header {HeaderName} was present but not a bearer token.", serviceAuthOptions.HeaderName);
                    }
                }
                else
                {
                    logger.LogWarning("Service authentication header {HeaderName} was missing.", serviceAuthOptions.HeaderName);
                }

                return Task.CompletedTask;
            },
            OnAuthenticationFailed = context =>
            {
                var logger = context.HttpContext.RequestServices.GetRequiredService<ILoggerFactory>().CreateLogger("BackendApi.ServiceAuth");
                logger.LogWarning(context.Exception, "Service token authentication failed: {Message}", context.Exception.Message);
                return Task.CompletedTask;
            }
        };
    });

builder.Services.AddAuthorization();
builder.Services.AddSingleton<TokenCredential, DefaultAzureCredential>();
builder.Services.AddSingleton(_ => new SystemTextJsonCosmosSerializer(new JsonSerializerOptions(JsonSerializerDefaults.Web)
{
    PropertyNameCaseInsensitive = true
}));
builder.Services.AddSingleton<ITenantDirectory, CosmosTenantDirectory>();
builder.Services.AddSingleton<ICosmosClientFactory, ManagedIdentityCosmosClientFactory>();
builder.Services.AddScoped<IAssetRepository, CosmosAssetRepository>();
builder.Services.AddScoped<IFrontendServiceAuthenticator, FrontendServiceAuthenticator>();

var app = builder.Build();

app.UseAuthentication();
app.UseAuthorization();

app.MapGet("/health", () => Results.Ok(new { status = "healthy" }));

app.MapGet("/internal/tenants/{tenantId}/portfolios", BackendHandlers.ListPortfoliosAsync)
    .RequireAuthorization();

app.MapGet("/internal/tenants/{tenantId}/portfolios/{portfolioId}/positions/{positionId}", BackendHandlers.GetPositionAsync)
    .RequireAuthorization();

app.MapPost("/internal/tenants/{tenantId}/transactions/{transactionId}/approve", BackendHandlers.ApproveTransactionAsync)
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
