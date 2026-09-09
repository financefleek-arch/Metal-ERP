using System.Net.Http.Headers;
using Microsoft.Extensions.Logging;

namespace TallyAgent.Tally;

/// <summary>
/// Wraps TallyPrime's XML HTTP Gateway (default http://localhost:9000).
///
/// Critical constraint (confirmed against Tally's own docs): the Gateway
/// only answers while TallyPrime is open with a company loaded on this PC —
/// it is not a background service. Any caller MUST check
/// <see cref="IsReachableAsync"/> first and treat "unreachable" as a normal,
/// expected state (queue the work, retry next poll) — never as an error.
/// This is why every Gateway-dependent module reports "tally_not_open"
/// distinctly from a genuine failure in its checkin status.
/// </summary>
public sealed class TallyGatewayClient(HttpClient http, ILogger<TallyGatewayClient> log)
{
    public async Task<bool> IsReachableAsync(string baseUrl, CancellationToken ct)
    {
        try
        {
            using var req = new HttpRequestMessage(HttpMethod.Post, baseUrl)
            {
                Content = new StringContent(EmptyExportEnvelope, System.Text.Encoding.UTF8)
                {
                    Headers = { ContentType = new MediaTypeHeaderValue("text/xml") },
                },
            };
            using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            cts.CancelAfter(TimeSpan.FromSeconds(5));
            var resp = await http.SendAsync(req, cts.Token);
            return resp.IsSuccessStatusCode;
        }
        catch (Exception ex)
        {
            log.LogDebug(ex, "Tally Gateway not reachable at {BaseUrl}", baseUrl);
            return false;
        }
    }

    /// <summary>Cheap probe classifying WHY the gateway is or isn't usable,
    /// for the checkin heartbeat's second signal (independent of any actual
    /// job). Reuses the same minimal "List of Companies" envelope as
    /// <see cref="IsReachableAsync"/> — one request, three outcomes:
    /// connected / no_company / refused (unknown as a last resort).</summary>
    public async Task<(bool Reachable, string Reason)> ProbeAsync(string baseUrl, CancellationToken ct)
    {
        try
        {
            using var req = new HttpRequestMessage(HttpMethod.Post, baseUrl)
            {
                Content = new StringContent(EmptyExportEnvelope, System.Text.Encoding.UTF8)
                {
                    Headers = { ContentType = new MediaTypeHeaderValue("text/xml") },
                },
            };
            using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            cts.CancelAfter(TimeSpan.FromSeconds(5));
            var resp = await http.SendAsync(req, cts.Token);
            if (!resp.IsSuccessStatusCode)
                return (false, "unknown");
            var body = await resp.Content.ReadAsStringAsync(cts.Token);
            if (body.Contains("Could not find Company", StringComparison.OrdinalIgnoreCase))
                return (false, "no_company");
            return (true, "connected");
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            log.LogDebug(ex, "Tally Gateway probe failed at {BaseUrl}", baseUrl);
            return (false, "refused");
        }
        catch (Exception ex)
        {
            log.LogDebug(ex, "Tally Gateway probe errored at {BaseUrl}", baseUrl);
            return (false, "unknown");
        }
    }

    public async Task<string> ExportAsync(string baseUrl, string requestXml, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, baseUrl)
        {
            Content = new StringContent(requestXml, System.Text.Encoding.UTF8, "text/xml"),
        };
        var resp = await http.SendAsync(req, ct);
        resp.EnsureSuccessStatusCode();
        return await resp.Content.ReadAsStringAsync(ct);
    }

    public async Task<string> ImportAsync(string baseUrl, string requestXml, CancellationToken ct) =>
        await ExportAsync(baseUrl, requestXml, ct); // same HTTP shape; Tally distinguishes by envelope content

    // A minimal, cheap "List of Companies" request — enough to prove the
    // Gateway answers without needing to know a company name up front.
    private const string EmptyExportEnvelope = """
        <ENVELOPE>
          <HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER>
          <BODY>
            <EXPORTDATA>
              <REQUESTDESC>
                <REPORTNAME>List of Companies</REPORTNAME>
              </REQUESTDESC>
            </EXPORTDATA>
          </BODY>
        </ENVELOPE>
        """;
}
