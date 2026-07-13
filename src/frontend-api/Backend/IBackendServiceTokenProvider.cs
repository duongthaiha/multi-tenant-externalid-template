namespace Contoso.AssetManagement.FrontendApi.Backend;

public interface IBackendServiceTokenProvider
{
    Task<string> GetServiceTokenAsync(CancellationToken cancellationToken);
}
