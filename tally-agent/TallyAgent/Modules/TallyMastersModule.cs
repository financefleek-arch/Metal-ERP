using System.Text.Json;
using Microsoft.Extensions.Options;
using TallyAgent.Tally;

namespace TallyAgent.Modules;

/// <summary>
/// F1a — Tally Connector, masters-in. Drains `module == "tally"` outbox
/// items the backend queued from a "Pull masters" click, exports the Tally
/// company's masters as XML, uploads it to R2, and reports the sync job's
/// result.
///
/// Transport is the Tally HTTP gateway (verified: a standard TallyPrime
/// running as Server on :9000 answers an "All Masters" export with a full
/// masters XML). `ExportDir` is an optional fallback for a shop that won't
/// enable "acts as Server".
///
/// Two conditions are NOT errors — they leave the job untouched so a later
/// poll retries: the gateway is unreachable (Tally closed), and no company
/// is loaded ("Could not find Company ''"). Only a genuine upload/report
/// failure posts an error result.
/// </summary>
public sealed class TallyMastersModule(
    IOptions<AgentOptions> options,
    TallyGatewayClient gateway) : IAgentModule
{
    private readonly TallyMastersOptions? _opts = options.Value.TallyMasters;

    public string Name => "tally";
    public TimeSpan PollInterval => TimeSpan.FromMinutes(_opts?.PollIntervalMinutes ?? 1);

    // Export "List of Accounts / All Masters" for a named company as XML.
    // {0} = company name (may be empty -> Tally uses the open company).
    private const string ExportEnvelopeTemplate =
        "<ENVELOPE>" +
        "<HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>" +
        "<TYPE>Data</TYPE><ID>List of Accounts</ID></HEADER>" +
        "<BODY><DESC><STATICVARIABLES>" +
        "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>" +
        "{0}" +
        "<ACCOUNTTYPE>All Masters</ACCOUNTTYPE>" +
        "</STATICVARIABLES></DESC></BODY>" +
        "</ENVELOPE>";

    public async Task RunOnceAsync(AgentContext ctx, CancellationToken ct)
    {
        var log = ctx.CreateLogger("TallyMasters");
        if (_opts is null || !_opts.Enabled)
        {
            ctx.ReportModuleStatus(Name, "disabled");
            return;
        }

        var jobs = ctx.PendingOutbox
            .Where(o => o.Module == "tally"
                        && GetString(o.Payload, "action") == "pull_masters")
            .ToList();

        if (jobs.Count == 0)
        {
            ctx.ReportModuleStatus(Name, "idle");
            return;
        }

        foreach (var item in jobs)
        {
            ct.ThrowIfCancellationRequested();
            var jobId = GetString(item.Payload, "job_id");
            if (string.IsNullOrEmpty(jobId))
            {
                log.LogWarning("tally outbox item {Id} has no job_id — skipping", item.Id);
                continue;
            }
            var companyName = GetString(item.Payload, "company_name") ?? "";
            await ProcessOneAsync(ctx, log, jobId, companyName, ct);
        }
    }

    private async Task ProcessOneAsync(
        AgentContext ctx, ILogger log, string jobId, string companyName, CancellationToken ct)
    {
        byte[]? xml = null;
        string sourceName = $"masters-{DateTime.UtcNow:yyyyMMddTHHmmssZ}.xml";

        // 1. gateway export
        try
        {
            var companyVar = string.IsNullOrWhiteSpace(companyName)
                ? ""
                : $"<SVCURRENTCOMPANY>{System.Security.SecurityElement.Escape(companyName)}</SVCURRENTCOMPANY>";
            var envelope = string.Format(ExportEnvelopeTemplate, companyVar);
            var body = await gateway.ExportAsync(_opts!.GatewayUrl, envelope, ct);

            if (body.Contains("Could not find Company", StringComparison.OrdinalIgnoreCase))
            {
                log.LogInformation("Tally has no company loaded — job {Job} will retry", jobId);
                ctx.ReportModuleStatus(Name, "no_company_loaded");
                await PingAsync(ctx, log, jobId, "no_company_loaded", ct);
                return;  // leave the job queued
            }
            if (!body.Contains("<STATUS>1</STATUS>", StringComparison.OrdinalIgnoreCase))
            {
                log.LogWarning("Tally export returned an unexpected body for job {Job}", jobId);
                // fall through to the folder fallback if configured
            }
            else
            {
                xml = System.Text.Encoding.UTF8.GetBytes(body);
            }
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            log.LogInformation(ex, "Tally gateway unreachable — job {Job} will retry", jobId);
            ctx.ReportModuleStatus(Name, "tally_unavailable");
            if (string.IsNullOrWhiteSpace(_opts!.ExportDir))
            {
                await PingAsync(ctx, log, jobId, "tally_unavailable", ct);
                return;
            }
            // else: try the folder fallback below before giving up
        }

        // 2. folder fallback
        if (xml is null && !string.IsNullOrWhiteSpace(_opts!.ExportDir))
        {
            try
            {
                var newest = new DirectoryInfo(_opts.ExportDir)
                    .EnumerateFiles("*.xml", SearchOption.TopDirectoryOnly)
                    .OrderByDescending(f => f.LastWriteTimeUtc)
                    .FirstOrDefault();
                if (newest is not null)
                {
                    xml = await File.ReadAllBytesAsync(newest.FullName, ct);
                    sourceName = newest.Name;
                    log.LogInformation("Using folder fallback {File} for job {Job}", newest.Name, jobId);
                }
            }
            catch (Exception ex)
            {
                log.LogWarning(ex, "folder fallback read failed for job {Job}", jobId);
            }
        }

        if (xml is null || xml.Length == 0)
        {
            // Neither transport produced anything. The module status set
            // above stands; the job stays queued for the next poll and the
            // status ping (if any) has already told the backend why.
            ctx.ReportModuleStatus(Name, "tally_unavailable");
            await PingAsync(ctx, log, jobId, "tally_unavailable", ct);
            return;
        }

        // 3. upload + report
        try
        {
            var tmp = Path.Combine(Path.GetTempPath(), $"tally-{jobId}-{sourceName}");
            await File.WriteAllBytesAsync(tmp, xml, ct);
            try
            {
                var req = await ctx.Backend.RequestUploadAsync(sourceName, xml.Length, ct);
                if (req is null)
                {
                    await ctx.Backend.PostJobResultAsync(
                        jobId, "error", null, "upload-request returned no response", ct);
                    ctx.ReportModuleStatus(Name, "error");
                    return;
                }
                await ctx.Backend.PutFileAsync(req.PutUrl, tmp, ct);
                await ctx.Backend.PostJobResultAsync(jobId, "ok", req.R2Key, null, ct);
                ctx.ReportModuleStatus(Name, "ok");
                log.LogInformation(
                    "Uploaded masters XML ({Bytes} bytes) for job {Job}", xml.Length, jobId);
            }
            finally
            {
                try { File.Delete(tmp); } catch { /* best effort */ }
            }
        }
        catch (Exception ex)
        {
            log.LogError(ex, "upload/report failed for job {Job}", jobId);
            try
            {
                await ctx.Backend.PostJobResultAsync(jobId, "error", null, ex.Message, ct);
            }
            catch (Exception reportEx)
            {
                log.LogError(reportEx, "could not even post the error result for job {Job}", jobId);
            }
            ctx.ReportModuleStatus(Name, "error");
        }
    }

    private static async Task PingAsync(
        AgentContext ctx, ILogger log, string jobId, string agentStatus, CancellationToken ct)
    {
        try
        {
            await ctx.Backend.PostJobStatusAsync(jobId, agentStatus, ct);
        }
        catch (Exception ex)
        {
            // Non-fatal — the job just stays queued without a "why". A later
            // poll pings again.
            log.LogDebug(ex, "status ping ({Status}) failed for job {Job}", agentStatus, jobId);
        }
    }

    private static string? GetString(Dictionary<string, object?> payload, string key)
    {
        if (!payload.TryGetValue(key, out var v) || v is null)
            return null;
        return v switch
        {
            string s => s,
            JsonElement e when e.ValueKind == JsonValueKind.String => e.GetString(),
            JsonElement e => e.ToString(),
            _ => v.ToString(),
        };
    }
}
