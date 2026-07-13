using System.Text.Json;
using Microsoft.Azure.Cosmos;

namespace Contoso.AssetManagement.BackendApi.Infrastructure;

public sealed class SystemTextJsonCosmosSerializer(JsonSerializerOptions jsonSerializerOptions) : CosmosSerializer
{
    private readonly JsonSerializerOptions jsonSerializerOptions = jsonSerializerOptions;

    public override T FromStream<T>(Stream stream)
    {
        using (stream)
        {
            if (stream.Length == 0)
            {
                return default!;
            }

            return JsonSerializer.Deserialize<T>(stream, jsonSerializerOptions)!;
        }
    }

    public override Stream ToStream<T>(T input)
    {
        var stream = new MemoryStream();
        JsonSerializer.Serialize(stream, input, jsonSerializerOptions);
        stream.Position = 0;
        return stream;
    }
}
