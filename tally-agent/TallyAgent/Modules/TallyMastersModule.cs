using System.Text.Json;
using Microsoft.Extensions.Options;
using TallyAgent.Tally;

namespace TallyAgent.Modules;

/// <summary>
/// F1a (masters-in) + F1b-1 (sales-voucher-out) + F5d (purchase-voucher-out).
/// Drains `module == "tally"` outbox items:
///   - `action == "pull_masters"` — exports the Tally company's masters as
///     XML, uploads it to R2, reports the sync job's result.
///   - `action == "push_sales"` / `action == "push_purchase"` — POSTs the
///     backend-built voucher XML (already sitting in
///     `payload["voucher_xml"]`, no second fetch) straight to the Tally
///     gateway, reports Tally's own response body back inline (no R2 — a
///     voucher response is a few KB). Both actions share one code path
///     (`ProcessPushAsync`) — the agent never inspects the voucher type,
///     it's pure transport either way.
///
/// Transport is the Tally HTTP gateway (verified live for both directions:
/// a standard TallyPrime running as Server on :9000 answers an "All
/// Masters" export with a full masters XML, and accepts an Import-Data
/// voucher POST and creates a real voucher — see
/// docs/EXECUTION-PLAN-F1b1-sales-voucher-push.md, "Live probe findings").
/// `ExportDir` is an optional fallback for pull only — a shop that won't
/// enable "acts as Server" has no equivalent fallback for push (there is
/// no manual-file path for vouchers in this slice).
///
/// Two conditions are NOT errors for either action — they leave the job
/// untouched so a later poll retries: the gateway is unreachable (Tally
/// closed), and no company is loaded ("Could not find Company ''"). Only a
/// genuine send/report failure posts an error result.
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

        // Standing health probe — independent of whether there's a job to
        // run. Feeds the checkin heartbeat's second signal ("agent -> Tally"),
        // separate from "agent -> Fleek" which the checkin itself proves.
        await ProbeReachabilityAsync(ctx, log, ct);

        var jobs = ctx.PendingOutbox
            .Where(o => o.Module == "tally")
            .ToList();

        if (jobs.Count == 0)
        {
            ctx.ReportModuleStatus(Name, "idle");
            return;
        }

        var anyError = false;
        foreach (var item in jobs)
        {
            ct.ThrowIfCancellationRequested();
            var jobId = GetString(item.Payload, "job_id");
            if (string.IsNullOrEmpty(jobId))
            {
                log.LogWarning("tally outbox item {Id} has no job_id — skipping", item.Id);
                continue;
            }
            var action = GetString(item.Payload, "action");
            bool ok;
            switch (action)
            {
                case "pull_masters":
                    ok = await ProcessPullAsync(
                        ctx, log, jobId, GetString(item.Payload, "company_name") ?? "", ct);
                    break;
                case "push_sales":
                case "push_purchase":
                    ok = await ProcessPushAsync(
                        ctx, log, jobId, GetString(item.Payload, "voucher_xml"), ct);
                    break;
                default:
                    log.LogWarning(
                        "tally outbox item {Id} has unknown action {Action} — skipping",
                        item.Id, action);
                    continue;
            }
            anyError = anyError || !ok;
        }

        // "ok"/"idle" is set per-job inside each Process*Async; only escalate
        // to a round-level "error" status if nothing else already reported
        // ok this round (a mixed batch still shows the most useful signal).
        if (!anyError && jobs.Count > 0)
        {
            ctx.ReportModuleStatus(Name, "ok");
        }
    }

    private async Task ProbeReachabilityAsync(AgentContext ctx, ILogger log, CancellationToken ct)
    {
        try
        {
            var (reachable, reason) = await gateway.ProbeAsync(_opts!.GatewayUrl, ct);
            ctx.SetTallyReachable(reachable, reason);
        }
        catch (Exception ex)
        {
            // Never let the health probe itself take down the module round.
            log.LogDebug(ex, "Tally reachability probe threw unexpectedly");
            ctx.SetTallyReachable(false, "unknown");
        }
    }

    /// <summary>Returns true if the job reached a definitive "ok" outcome
    /// this round (used only to pick the round's overall module status —
    /// a job left queued for retry, or a real error, both return false).</summary>
    private async Task<bool> ProcessPullAsync(
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
                return false;  // leave the job queued
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
                return false;
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
            return false;
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
                    return false;
                }
                await ctx.Backend.PutFileAsync(req.PutUrl, tmp, ct);
                await ctx.Backend.PostJobResultAsync(jobId, "ok", req.R2Key, null, ct);
                log.LogInformation(
                    "Uploaded masters XML ({Bytes} bytes) for job {Job}", xml.Length, jobId);
                return true;
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
            return false;
        }
    }

    /// <summary>
    /// F1b-1 / F5d — push a Sales or Purchase voucher. The XML to send is
    /// already built by the backend and sitting in the outbox payload
    /// (`voucher_xml`); this method's only job is to POST it and relay
    /// Tally's response back — it never inspects the voucher type, so the
    /// same code path serves both `push_sales` and `push_purchase`. Same
    /// "not reachable / no company" handling as pull, reusing the same
    /// status-ping vocabulary the backend already understands.
    /// </summary>
    private async Task<bool> ProcessPushAsync(
        AgentContext ctx, ILogger log, string jobId, string? voucherXml, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(voucherXml))
        {
            log.LogWarning("push job {Job} has no voucher_xml in its payload", jobId);
            try
            {
                await ctx.Backend.PostJobResultAsync(
                    jobId, "error", null, "agent received a push job with no voucher XML", ct);
            }
            catch (Exception ex)
            {
                log.LogError(ex, "could not report missing-voucher error for job {Job}", jobId);
            }
            ctx.ReportModuleStatus(Name, "error");
            return false;
        }

        string body;
        try
        {
            body = await gateway.ImportAsync(_opts!.GatewayUrl, voucherXml, ct);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            log.LogInformation(ex, "Tally gateway unreachable — push job {Job} will retry", jobId);
            ctx.ReportModuleStatus(Name, "tally_unavailable");
            await PingAsync(ctx, log, jobId, "tally_unavailable", ct);
            return false;
        }

        if (body.Contains("Could not find Company", StringComparison.OrdinalIgnoreCase))
        {
            log.LogInformation("Tally has no company loaded — push job {Job} will retry", jobId);
            ctx.ReportModuleStatus(Name, "no_company_loaded");
            await PingAsync(ctx, log, jobId, "no_company_loaded", ct);
            return false;
        }

        // Whatever Tally actually said — success, a LINEERROR, or something
        // unexpected — goes straight back to the backend as-is. The backend
        // (services/tally/push.py::process_push_result) owns interpreting
        // <CREATED>/<LINEERROR>, not the agent; the agent's job is transport.
        try
        {
            await ctx.Backend.PostJobResultAsync(jobId, "ok", null, null, ct, tallyResponse: body);
            log.LogInformation("Reported push result for job {Job} ({Bytes} bytes)", jobId, body.Length);
            return true;
        }
        catch (Exception ex)
        {
            log.LogError(ex, "could not report push result for job {Job}", jobId);
            ctx.ReportModuleStatus(Name, "error");
            return false;
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
