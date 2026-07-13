using System.Net;
using Contoso.AssetManagement.FrontendApi.Models;

namespace Contoso.AssetManagement.FrontendApi.Agent;

public sealed record AgentChatResult(bool IsSuccess, HttpStatusCode StatusCode, AgentChatResponse? Value, string? Error)
{
    public static AgentChatResult Success(HttpStatusCode statusCode, AgentChatResponse value) => new(true, statusCode, value, null);

    public static AgentChatResult Failure(HttpStatusCode statusCode, string error) => new(false, statusCode, null, error);
}
