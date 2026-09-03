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
