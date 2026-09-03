using Microsoft.Extensions.Options;
using TallyAgent.State;

namespace TallyAgent.Modules;

/// <summary>
/// Reuses Backup Sync's own confirmed-upload record (no separate file watch
/// needed) to detect "Tally's own scheduled backup silently stopped" —
/// independent of whether the last backup that *did* land also made it to
/// the cloud. If no confirmed upload has happened within
/// <see cref="BackupHealthMonitorOptions.ExpectedIntervalHours"/>, reports
/// backup_stalled so it surfaces in the admin view even before a human
/// notices at the shop.
/// </summary>
public sealed class BackupHealthMonitorModule(
    IOptions<AgentOptions> options,
    AgentStateStore state) : IAgentModule
{
    private readonly BackupHealthMonitorOptions? _opts = options.Value.BackupHealthMonitor;

    public string Name => "backup_health";
    public TimeSpan PollInterval => TimeSpan.FromMinutes(_opts?.PollIntervalMinutes ?? 15);

    public Task RunOnceAsync(AgentContext ctx, CancellationToken ct)
    {
        if (_opts is null || !_opts.Enabled)
        {
            ctx.ReportModuleStatus(Name, "disabled");
            return Task.CompletedTask;
        }

        var lastConfirmed = state.MostRecentConfirmedUtc();
        var window = TimeSpan.FromHours(_opts.ExpectedIntervalHours);

        if (lastConfirmed is null)
        {
            // No confirmed upload yet at all — not necessarily stalled (could
            // be a brand-new install); only flag once the window has passed
            // since the module started reporting, which the backend's own
            // "no checkin/upload ever" view already covers via last_upload_at.
            ctx.ReportModuleStatus(Name, "no_backup_yet");
            return Task.CompletedTask;
        }

        var status = DateTime.UtcNow - lastConfirmed.Value > window ? "backup_stalled" : "ok";
        ctx.ReportModuleStatus(Name, status);
        if (status == "backup_stalled")
        {
            ctx.ReportError(
                $"No confirmed backup upload since {lastConfirmed.Value:u} " +
                $"(expected within {_opts.ExpectedIntervalHours}h) — check Tally's scheduled backup.");
        }
        return Task.CompletedTask;
    }
}
