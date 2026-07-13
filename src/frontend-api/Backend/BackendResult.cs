using System.Net;

namespace Contoso.AssetManagement.FrontendApi.Backend;

public sealed record BackendResult<T>(
    HttpStatusCode StatusCode,
    T? Value,
    string? Error = null)
{
    public bool IsSuccess => (int)StatusCode is >= 200 and <= 299;

    public static BackendResult<T> Success(HttpStatusCode statusCode, T value) => new(statusCode, value);

    public static BackendResult<T> Failure(HttpStatusCode statusCode, string error) => new(statusCode, default, error);
}
