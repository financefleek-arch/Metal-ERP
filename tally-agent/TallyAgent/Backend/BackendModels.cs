using System.Text.Json.Serialization;

namespace TallyAgent.Backend;

public sealed class CheckinRequest
{
    [JsonPropertyName("module_status")]
    public Dictionary<string, string> ModuleStatus { get; set; } = new();

    [JsonPropertyName("error")]
    public string? Error { get; set; }

    // Second signal, independent of module_status: can this agent reach
    // TallyPrime's HTTP gateway right now? Null = not probed this round.
    [JsonPropertyName("tally_reachable")]
    public bool? TallyReachable { get; set; }

    // connected | refused | no_company | unknown
    [JsonPropertyName("tally_reason")]
    public string? TallyReason { get; set; }

    // Auto-update: what this build is, and (once, after a rolled-back or
    // rejected update) which version failed and why.
    [JsonPropertyName("agent_version")]
    public string? AgentVersion { get; set; }

    [JsonPropertyName("os_version")]
    public string? OsVersion { get; set; }

    [JsonPropertyName("update_failed_version")]
    public string? UpdateFailedVersion { get; set; }

    [JsonPropertyName("update_error")]
    public string? UpdateError { get; set; }
}

public sealed class CheckinResponse
{
    [JsonPropertyName("shop_id")]
    public string ShopId { get; set; } = "";

    [JsonPropertyName("checked_in_at")]
    public DateTimeOffset CheckedInAt { get; set; }

    [JsonPropertyName("outbox")]
    public List<OutboxItem> Outbox { get; set; } = new();

    [JsonPropertyName("update")]
    public UpdateOffer? Update { get; set; }
}

/// <summary>A build the backend wants this agent on. Trust comes from
/// <see cref="Signature"/>, verified against keys compiled into the agent.</summary>
public sealed class UpdateOffer
{
    [JsonPropertyName("version")]
    public string Version { get; set; } = "";

    [JsonPropertyName("url")]
    public string Url { get; set; } = "";

    [JsonPropertyName("sha256")]
    public string Sha256 { get; set; } = "";

    [JsonPropertyName("size_bytes")]
    public long SizeBytes { get; set; }

    [JsonPropertyName("signature")]
    public string Signature { get; set; } = "";
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

    /// <summary>Groups the files of one Tally backup run (manifest + data
    /// parts) so cloud retention prunes whole sets, never a partial. Null
    /// for a standalone upload (e.g. the masters-XML pull).</summary>
    [JsonPropertyName("set_id")]
    public string? SetId { get; set; }
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

public sealed class JobResultRequest
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = "ok";  // "ok" | "error"

    [JsonPropertyName("r2_key")]
    public string? R2Key { get; set; }

    // F1b-1 (push_sales): Tally's own small Import-Data <RESPONSE> XML,
    // sent back inline — no R2 round-trip for a voucher result the way
    // pull's masters XML needs one.
    [JsonPropertyName("tally_response")]
    public string? TallyResponse { get; set; }

    [JsonPropertyName("error")]
    public string? Error { get; set; }
}

public sealed class JobStatusPingRequest
{
    // "no_company_loaded" | "tally_unavailable"
    [JsonPropertyName("agent_status")]
    public string AgentStatus { get; set; } = "";
}
