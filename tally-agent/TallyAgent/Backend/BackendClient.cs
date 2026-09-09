using System.Net.Http.Headers;
using System.Net.Http.Json;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace TallyAgent.Backend;

/// <summary>
/// Talks to /api/tally-agent/* on the Metal ERP backend. Every call carries
/// this shop's API key in X-Shop-Key — a distinct, machine-to-machine auth
/// scheme from the human JWT login the rest of Metal ERP uses.
/// </summary>
public sealed class BackendClient
{
    private readonly HttpClient _http;
    private readonly ILogger<BackendClient> _log;

    public BackendClient(HttpClient http, IOptions<AgentOptions> options, ILogger<BackendClient> log)
    {
        _http = http;
        _log = log;
        var opts = options.Value;
        _http.BaseAddress = new Uri(opts.BackendBaseUrl.TrimEnd('/') + "/");
        _http.DefaultRequestHeaders.Add("X-Shop-Key", opts.ShopApiKey);
        _http.Timeout = TimeSpan.FromSeconds(30);
    }

    public async Task<CheckinResponse?> CheckinAsync(
        Dictionary<string, string> moduleStatus,
        string? error,
        bool? tallyReachable,
        string? tallyReason,
        CancellationToken ct)
    {
        try
        {
            var resp = await _http.PostAsJsonAsync(
                "api/tally-agent/checkin",
                new CheckinRequest
                {
                    ModuleStatus = moduleStatus,
                    Error = error,
                    TallyReachable = tallyReachable,
                    TallyReason = tallyReason,
                },
                ct);

            if (!resp.IsSuccessStatusCode)
            {
                // Log the real status + body BEFORE throwing — EnsureSuccessStatusCode's
                // exception message alone doesn't carry the response body, and a bare
                // "checkin failed" warning gives no way to tell a stale/rotated key
                // (401) apart from a network/server problem without this.
                var body = await SafeReadBodyAsync(resp, ct);
                _log.LogWarning(
                    "checkin rejected: {StatusCode} {ReasonPhrase} - {Body}",
                    (int)resp.StatusCode, resp.ReasonPhrase, body);
                resp.EnsureSuccessStatusCode();
            }

            var result = await resp.Content.ReadFromJsonAsync<CheckinResponse>(cancellationToken: ct);
            _log.LogInformation(
                "checkin ok - shop {ShopId}, {OutboxCount} outbox item(s)",
                result?.ShopId, result?.Outbox.Count ?? 0);
            return result;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "checkin failed - could not reach {BackendBaseUrl}", _http.BaseAddress);
            return null;
        }
    }

    public async Task<UploadRequestResponse?> RequestUploadAsync(
        string filename, long sizeBytes, CancellationToken ct)
    {
        var resp = await _http.PostAsJsonAsync(
            "api/tally-agent/upload-request",
            new UploadRequestRequest { Filename = filename, SizeBytes = sizeBytes },
            ct);
        resp.EnsureSuccessStatusCode();
        return await resp.Content.ReadFromJsonAsync<UploadRequestResponse>(cancellationToken: ct);
    }

    public async Task ConfirmUploadAsync(string uploadId, string status, CancellationToken ct)
    {
        var resp = await _http.PostAsJsonAsync(
            "api/tally-agent/upload-confirm",
            new UploadConfirmRequest { UploadId = uploadId, Status = status },
            ct);
        resp.EnsureSuccessStatusCode();
    }

    /// <summary>
    /// Report a tally_sync_job's outcome. status "ok" carries the R2 key of
    /// the uploaded masters XML (the backend then downloads + parses it);
    /// status "error" carries a short message. The backend never 500s here,
    /// so a non-success status code means a real problem (wrong shop, bad
    /// job id) — log and move on, a later poll re-dispatches the job.
    /// </summary>
    public async Task PostJobResultAsync(
        string jobId, string status, string? r2Key, string? error, CancellationToken ct)
    {
        var resp = await _http.PostAsJsonAsync(
            $"api/tally-agent/jobs/{jobId}/result",
            new JobResultRequest { Status = status, R2Key = r2Key, Error = error },
            ct);
        resp.EnsureSuccessStatusCode();
    }

    /// <summary>
    /// Ping a "not ready" reason for a tally_sync_job — Tally is closed
    /// ("tally_unavailable") or open but with no company loaded
    /// ("no_company_loaded"). The job stays queued for a later retry; this
    /// just lets the operator console show "Waiting on Tally" and lets the
    /// backend auto-cancel a pull that never gets a real result.
    /// </summary>
    public async Task PostJobStatusAsync(string jobId, string agentStatus, CancellationToken ct)
    {
        var resp = await _http.PostAsJsonAsync(
            $"api/tally-agent/jobs/{jobId}/status",
            new JobStatusPingRequest { AgentStatus = agentStatus },
            ct);
        resp.EnsureSuccessStatusCode();
    }

    /// <summary>Direct PUT of the file bytes to the pre-signed R2 URL — not a
    /// backend call, but lives here since it completes the same upload flow.</summary>
    public async Task PutFileAsync(string putUrl, string localFilePath, CancellationToken ct)
    {
        using var stream = File.OpenRead(localFilePath);
        using var content = new StreamContent(stream);
        content.Headers.ContentLength = stream.Length;
        content.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");

        // A fresh, unauthenticated client — the pre-signed URL carries its
        // own auth in the query string; sending X-Shop-Key here would be
        // meaningless to R2 and the URL's signature doesn't cover this header.
        using var plain = new HttpClient { Timeout = TimeSpan.FromMinutes(30) };
        var resp = await plain.PutAsync(putUrl, content, ct);
        resp.EnsureSuccessStatusCode();
    }

    /// <summary>Best-effort response-body read for logging a failure — never
    /// throws (a body read can itself fail on a truncated/streamed response),
    /// truncated to keep one log line readable.</summary>
    private static async Task<string> SafeReadBodyAsync(HttpResponseMessage resp, CancellationToken ct)
    {
        try
        {
            var body = await resp.Content.ReadAsStringAsync(ct);
            return body.Length > 500 ? body[..500] + "…" : body;
        }
        catch
        {
            return "<could not read response body>";
        }
    }
}
