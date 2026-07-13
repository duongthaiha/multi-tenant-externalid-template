using Azure.Identity;
using Microsoft.Azure.Cosmos;
using Microsoft.Azure.Functions.Worker;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Contoso.AssetManagement.CustomClaimsProvider.Services;

var host = new HostBuilder()
    .ConfigureFunctionsWorkerDefaults()
    .ConfigureAppConfiguration((context, config) =>
    {
        config.AddEnvironmentVariables();
    })
    .ConfigureServices((context, services) =>
    {
        services.AddApplicationInsightsTelemetryWorkerService();
        services.ConfigureFunctionsApplicationInsights();

        services.AddOptions<ControlPlaneCosmosOptions>()
            .Bind(context.Configuration.GetSection(ControlPlaneCosmosOptions.SectionName))
            .Validate(options => Uri.TryCreate(options.Endpoint, UriKind.Absolute, out _), "Control-plane Cosmos endpoint must be an absolute URI.")
            .Validate(options => options.RequestTimeoutMilliseconds is >= 500 and <= 2000, "Request timeout must be between 500ms and 2000ms.")
            .ValidateOnStart();

        services.AddSingleton(sp =>
        {
            var options = sp.GetRequiredService<Microsoft.Extensions.Options.IOptions<ControlPlaneCosmosOptions>>().Value;
            return new CosmosClient(options.Endpoint, new DefaultAzureCredential(), new CosmosClientOptions
            {
                ConnectionMode = ConnectionMode.Gateway,
                RequestTimeout = TimeSpan.FromMilliseconds(options.RequestTimeoutMilliseconds),
                SerializerOptions = new CosmosSerializationOptions
                {
                    PropertyNamingPolicy = CosmosPropertyNamingPolicy.CamelCase
                }
            });
        });

        services.AddSingleton<IEntitlementResolver, CosmosEntitlementResolver>();
    })
    .Build();

await host.RunAsync();
