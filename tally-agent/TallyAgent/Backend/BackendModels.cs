using System.Text.Json.Serialization;

namespace TallyAgent.Backend;

public sealed class CheckinRequest
{
    [JsonPropertyName("module_status")]
    public Dictionary<string, string> ModuleStatus { get; set; } = new();

    [JsonPropertyName("error")]
    public string? Error { get; set; }
}

public sealed class CheckinResponse
{
    [JsonPropertyName("shop_id")]
    public string ShopId { get; set; } = "";

    [JsonPropertyName("checked_in_at")]
    public DateTimeOffset CheckedInAt { get; set; }

    [JsonPropertyName("outbox")]
    public List<OutboxItem> Outbox { get; set; } = new();
}

public sealed class OutboxItem
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = "";

    [JsonPropertyName("module")]
    public string Module { get; set; } = "";

    [JsonPropertyName("payload")]
    public Dictionary<string, object?> Payload { get; set; } = new();
}

public sealed class UploadRequestRequest
{
    [JsonPropertyName("filename")]
    public string Filename { get; set; } = "";

    [JsonPropertyName("size_bytes")]
    public long SizeBytes { get; set; }
}

public sealed class UploadRequestResponse
{
    [JsonPropertyName("upload_id")]
    public string UploadId { get; set; } = "";

    [JsonPropertyName("put_url")]
    public string PutUrl { get; set; } = "";

    [JsonPropertyName("r2_key")]
    public string R2Key { get; set; } = "";

    [JsonPropertyName("expires_in")]
    public int ExpiresIn { get; set; }
}

public sealed class UploadConfirmRequest
{
    [JsonPropertyName("upload_id")]
    public string UploadId { get; set; } = "";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "confirmed";
}
