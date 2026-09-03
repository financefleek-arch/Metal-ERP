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
        Dictionary<string, string> moduleStatus, string? error, CancellationToken ct)
    {
        try
        {
            var resp = await _http.PostAsJsonAsync(
                "api/tally-agent/checkin",
                new CheckinRequest { ModuleStatus = moduleStatus, Error = error },
                ct);
            resp.EnsureSuccessStatusCode();
            return await resp.Content.ReadFromJsonAsync<CheckinResponse>(cancellationToken: ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "checkin failed");
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
}
