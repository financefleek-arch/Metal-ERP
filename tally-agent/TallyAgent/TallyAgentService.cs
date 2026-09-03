using TallyAgent.Backend;
using TallyAgent.Modules;
using TallyAgent.Tally;

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
    ILoggerFactory loggerFactory,
    ILogger<TallyAgentService> log) : BackgroundService
{
    private static readonly TimeSpan CheckinInterval = TimeSpan.FromMinutes(1);

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var moduleList = modules.ToList();
        log.LogInformation(
            "Tally Agent starting with {Count} module(s): {Names}",
            moduleList.Count, string.Join(", ", moduleList.Select(m => m.Name)));

        var ctx = new AgentContext(backend, tallyGateway, loggerFactory);
        var lastRun = new Dictionary<string, DateTimeOffset>();
        var lastCheckin = DateTimeOffset.MinValue;

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

            if (now - lastCheckin >= CheckinInterval)
            {
                await backend.CheckinAsync(
                    ctx.ModuleStatusSnapshot.ToDictionary(kv => kv.Key, kv => kv.Value),
                    ctx.LastErrorSnapshot,
                    stoppingToken);
                lastCheckin = now;
            }

            try
            {
                await Task.Delay(TimeSpan.FromSeconds(15), stoppingToken);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }
}
