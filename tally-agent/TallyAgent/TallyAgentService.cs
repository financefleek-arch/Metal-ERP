using System.Diagnostics;
using Microsoft.Extensions.Options;
using Serilog;
using TallyAgent.Backend;
using TallyAgent.Modules;
using TallyAgent.Tally;
using TallyAgent.Updates;

namespace TallyAgent;

/// <summary>
/// Host process: runs every enabled <see cref="IAgentModule"/> on its own
/// interval, and calls checkin after each full round so the backend's shop
/// status reflects the latest per-module state promptly. One module's
/// exception is caught and logged, never crashes the host or blocks
/// other modules — see <see cref="IAgentModule.RunOnceAsync"/>'s contract.
/// </summary>
public sealed class TallyAgentService(
    IEnumerable<IAgentModule> modules,
    BackendClient backend,
    TallyGatewayClient tallyGateway,
    UpdateCoordinator updates,
    IOptions<AgentOptions> options,
    ILoggerFactory loggerFactory,
    ILogger<TallyAgentService> log) : BackgroundService
{
    private static readonly TimeSpan CheckinInterval = TimeSpan.FromMinutes(1);

    // Up this long without crashing = a freshly swapped-in build is good.
    private static readonly TimeSpan HealthyAfter = TimeSpan.FromSeconds(90);
    private static readonly TimeSpan TransientRetry = TimeSpan.FromMinutes(15);

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var moduleList = modules.ToList();
        log.LogInformation(
            "Tally Agent starting with {Count} module(s): {Names}",
            moduleList.Count, string.Join(", ", moduleList.Select(m => m.Name)));

        var updatesOn = options.Value.Update.Enabled;
        if (updatesOn && updates.OnStartup() == StartupAction.ExitForRestart)
        {
            // Files were swapped (or rolled back). Windows can't run the new exe
            // from this process, so die: Task Scheduler / the SCM restarts us.
            log.LogInformation("Exiting so the restart picks up the new build");
            Exit(UpdateCoordinator.ExitCodeForRestart);
        }

        var ctx = new AgentContext(backend, tallyGateway, loggerFactory);
        var lastRun = new Dictionary<string, DateTimeOffset>();
        var lastCheckin = DateTimeOffset.MinValue;
        var uptime = Stopwatch.StartNew();
        var markedHealthy = false;
        var ignoredOffers = new HashSet<string>();
        var nextUpdateAttempt = DateTimeOffset.MinValue;
        using var downloadHttp = new HttpClient { Timeout = TimeSpan.FromMinutes(30) };

        while (!stoppingToken.IsCancellationRequested)
        {
            var now = DateTimeOffset.UtcNow;

            foreach (var module in moduleList)
            {
                var due = !lastRun.TryGetValue(module.Name, out var last)
                    || now - last >= module.PollInterval;
                if (!due) continue;

                try
                {
                    await module.RunOnceAsync(ctx, stoppingToken);
                }
                catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
                {
                    throw;
                }
                catch (Exception ex)
                {
                    log.LogError(ex, "Module {Module} threw an unexpected exception", module.Name);
                    ctx.ReportModuleStatus(module.Name, "error");
                    ctx.ReportError($"{module.Name}: unexpected error — {ex.Message}");
                }
                finally
                {
                    lastRun[module.Name] = now;
                }
            }

            if (updatesOn && !markedHealthy && uptime.Elapsed >= HealthyAfter)
            {
                updates.MarkHealthy();
                markedHealthy = true;
            }

            // A job just finished: check in now and run the Tally module again next loop, so a
            // multi-batch push does not wait out a full interval between batches.
            var fast = ctx.ConsumeFastCycle();
            if (fast)
            {
                lastCheckin = DateTimeOffset.MinValue;
                lastRun.Remove("tally");
            }

            if (now - lastCheckin >= CheckinInterval)
            {
                var failure = updatesOn ? updates.PendingFailure() : null;
                var resp = await backend.CheckinAsync(
                    ctx.ModuleStatusSnapshot.ToDictionary(kv => kv.Key, kv => kv.Value),
                    ctx.LastErrorSnapshot,
                    ctx.TallyReachable,
                    ctx.TallyReason,
                    stoppingToken,
                    AgentVersion.Current,
                    AgentVersion.OsDescription,
                    failure?.Version,
                    failure?.Error);
                // Hand this round's outbox to modules for the next round.
                ctx.SetPendingOutbox(resp?.Outbox ?? new List<Backend.OutboxItem>());
                lastCheckin = now;

                // Delivered once; the backend then stops offering that version.
                if (resp is not null && failure is not null) updates.ClearFailure();

                // Every module above ran to completion, so this is a safe moment:
                // nothing is mid-upload or mid-push.
                if (updatesOn && resp?.Update is { } offer
                    && !ignoredOffers.Contains(offer.Version) && now >= nextUpdateAttempt)
                {
                    var result = await updates.StageAsync(
                        offer, ReleaseKeys.PublicKeysSpkiBase64, downloadHttp, stoppingToken);
                    switch (result)
                    {
                        case StageResult.Staged:
                            log.LogInformation("Update {Version} staged; restarting to apply", offer.Version);
                            Exit(UpdateCoordinator.ExitCodeForRestart);
                            break;
                        case StageResult.Transient:
                            nextUpdateAttempt = now + TransientRetry;
                            break;
                        default: // Skipped / Rejected: don't re-evaluate this version every minute
                            ignoredOffers.Add(offer.Version);
                            break;
                    }
                }
            }

            try
            {
                await Task.Delay(TimeSpan.FromSeconds(fast ? 1 : 15), stoppingToken);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }

    private static void Exit(int code)
    {
        Log.CloseAndFlush();
        Environment.Exit(code);
    }
}
