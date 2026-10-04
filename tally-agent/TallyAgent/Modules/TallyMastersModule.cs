using System.Text.Json;
using Microsoft.Extensions.Options;
using TallyAgent.Backend;
using TallyAgent.Tally;

namespace TallyAgent.Modules;

/// <summary>
/// F1a (masters-in) + F1b-1 (sales-voucher-out) + F5d (purchase-voucher-out).
/// Drains `module == "tally"` outbox items:
///   - `action == "pull_masters"` — exports the Tally company's masters as
///     XML, uploads it to R2, reports the sync job's result.
///   - `action == "push_sales"` / `"push_purchase"` / `"push_items"` — POSTs the
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
/// Two conditions are NOT errors for either action: the gateway is unreachable
/// (Tally closed) and no company is loaded ("Could not find Company ''"). The
/// backend hands a Tally job to the agent exactly ONCE (it marks the outbox item
/// `sent` at that checkin), so the agent itself must keep such a job and retry it
/// every round until it succeeds or <see cref="RetryWindow"/> runs out - nothing
/// upstream will redeliver it. Retrying is only done when the request provably
/// never reached Tally (a connection-level failure, or no company open): a timeout
/// or an HTTP error might mean Tally already processed it, so those are reported
/// as an error and never replayed. The held jobs live in memory; if the agent is
/// restarted they are lost and the backend cancels the stalled job after 10
/// minutes so the user can send again. Only a genuine send/report failure posts an
/// error result.
/// </summary>
public sealed class TallyMastersModule(
    IOptions<AgentOptions> options,
    TallyGatewayClient gateway) : IAgentModule
{
    private readonly TallyMastersOptions? _opts = options.Value.TallyMasters;

    /// <summary>How long a job that found Tally "not ready" is retried. Must stay below the
    /// backend's 10-minute give-up window (tally_items.STALLED_AFTER / jobs._STUCK_AFTER).</summary>
    internal static readonly TimeSpan RetryWindow = TimeSpan.FromMinutes(8);

    private sealed record Held(OutboxItem Item, DateTimeOffset FirstSeen);

    /// <summary>Time source; tests replace it to step past <see cref="RetryWindow"/>.</summary>
    internal Func<DateTimeOffset> Clock { get; set; } = () => DateTimeOffset.UtcNow;

    // job_id -> the job, kept until it finishes or RetryWindow passes.
    private readonly Dictionary<string, Held> _held = new();

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

        var now = Clock();
        foreach (var o in ctx.PendingOutbox.Where(o => o.Module == "tally"))
        {
            var id = GetString(o.Payload, "job_id");
            if (string.IsNullOrEmpty(id))
            {
                log.LogWarning("tally outbox item {Id} has no job_id — skipping", o.Id);
                continue;
            }
            _held.TryAdd(id, new Held(o, now));
        }
        foreach (var (id, held) in _held.ToList())
        {
            if (now - held.FirstSeen > RetryWindow)
            {
                _held.Remove(id);
                log.LogWarning(
                    "Giving up on Tally job {Job} after {Minutes} min of Tally not being ready; "
                    + "the backend will cancel it so it can be sent again",
                    id, (int)RetryWindow.TotalMinutes);
            }
        }

        var jobs = _held.Values.Select(h => h.Item).ToList();

        if (jobs.Count == 0)
        {
            ctx.ReportModuleStatus(Name, "idle");
            return;
        }

        var anyError = false;
        foreach (var item in jobs)
        {
            ct.ThrowIfCancellationRequested();
            var jobId = GetString(item.Payload, "job_id")!;
            var action = GetString(item.Payload, "action");
            JobOutcome outcome;
            switch (action)
            {
                case "pull_masters":
                    outcome = await ProcessPullAsync(
                        ctx, log, jobId, GetString(item.Payload, "company_name") ?? "", ct);
                    break;
                case "push_sales":
                case "push_purchase":
                case "push_items":
                    outcome = await ProcessPushAsync(
                        ctx, log, jobId, GetString(item.Payload, "voucher_xml"), ct);
                    break;
                default:
                    log.LogWarning(
                        "tally outbox item {Id} has unknown action {Action} — skipping",
                        item.Id, action);
                    _held.Remove(jobId);
                    continue;
            }

            if (outcome == JobOutcome.Retry)
            {
                // Tally is not ready: keep the job, try again next round.
                log.LogInformation("Tally job {Job} is waiting for Tally; will retry", jobId);
                continue;
            }
            _held.Remove(jobId);
            anyError = anyError || outcome == JobOutcome.Failed;
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
    private async Task<JobOutcome> ProcessPullAsync(
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
                return JobOutcome.Retry;  // keep the job; retried next round
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
                return JobOutcome.Retry;
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
            return JobOutcome.Retry;
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
                    return JobOutcome.Failed;
                }
                await ctx.Backend.PutFileAsync(req.PutUrl, tmp, ct);
                await ctx.Backend.PostJobResultAsync(jobId, "ok", req.R2Key, null, ct);
                log.LogInformation(
                    "Uploaded masters XML ({Bytes} bytes) for job {Job}", xml.Length, jobId);
                return JobOutcome.Done;
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
            return JobOutcome.Failed;
        }
    }

    /// <summary>
    /// F1b-1 / F5d — push a Sales or Purchase voucher. The XML to send is
    /// already built by the backend and sitting in the outbox payload
    /// (`voucher_xml`); this method's only job is to POST it and relay
    /// Tally's response back — it never inspects the voucher type, so the
    /// same code path serves `push_sales`, `push_purchase` and `push_items`. Same
    /// "not reachable / no company" handling as pull, reusing the same
    /// status-ping vocabulary the backend already understands.
    /// </summary>
    private async Task<JobOutcome> ProcessPushAsync(
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
            return JobOutcome.Failed;
        }

        string body;
        try
        {
            body = await gateway.ImportAsync(_opts!.GatewayUrl, voucherXml, ct);
        }
        catch (HttpRequestException ex) when (ex.StatusCode is null)
        {
            // No response at all (connection refused / reset before Tally answered): the
            // batch never reached Tally, so it is safe to send it again next round.
            log.LogInformation(ex, "Tally gateway unreachable — push job {Job} will retry", jobId);
            ctx.ReportModuleStatus(Name, "tally_unavailable");
            await PingAsync(ctx, log, jobId, "tally_unavailable", ct);
            return JobOutcome.Retry;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            // Tally may have received - and even processed - this batch (a timeout, or an
            // HTTP error status), so it must NOT be replayed: that could create duplicates.
            // Tell the backend plainly and let the shop check Tally before sending again.
            var timedOut = ex is TaskCanceledException;
            var why = timedOut
                ? "Tally did not answer in time. It may still have saved this batch - open Tally "
                  + "and check before sending again."
                : $"Tally answered with an error ({(int?)((HttpRequestException)ex).StatusCode}). "
                  + "Check Tally before sending again.";
            log.LogWarning(ex, "push job {Job} not retried automatically: {Why}", jobId, why);
            ctx.ReportModuleStatus(Name, "error");
            try
            {
                await ctx.Backend.PostJobResultAsync(jobId, "error", null, why, ct);
            }
            catch (Exception reportEx)
            {
                log.LogError(reportEx, "could not report the failed push for job {Job}", jobId);
            }
            return JobOutcome.Failed;
        }

        if (body.Contains("Could not find Company", StringComparison.OrdinalIgnoreCase))
        {
            log.LogInformation("Tally has no company loaded — push job {Job} will retry", jobId);
            ctx.ReportModuleStatus(Name, "no_company_loaded");
            await PingAsync(ctx, log, jobId, "no_company_loaded", ct);
            return JobOutcome.Retry;
        }

        // Whatever Tally actually said — success, a LINEERROR, or something
        // unexpected — goes straight back to the backend as-is. The backend
        // (services/tally/push.py::process_push_result) owns interpreting
        // <CREATED>/<LINEERROR>, not the agent; the agent's job is transport.
        try
        {
            await ctx.Backend.PostJobResultAsync(jobId, "ok", null, null, ct, tallyResponse: body);
            log.LogInformation("Reported push result for job {Job} ({Bytes} bytes)", jobId, body.Length);
            return JobOutcome.Done;
        }
        catch (Exception ex)
        {
            log.LogError(ex, "could not report push result for job {Job}", jobId);
            ctx.ReportModuleStatus(Name, "error");
            return JobOutcome.Failed;
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

/// <summary>What happened to one Tally job this round.</summary>
internal enum JobOutcome
{
    /// <summary>Reached a definitive result (and it was reported to the backend).</summary>
    Done,
    /// <summary>Tally was not ready and the request never reached it: keep the job and retry.</summary>
    Retry,
    /// <summary>A real failure (already reported where possible): do not retry.</summary>
    Failed,
}
