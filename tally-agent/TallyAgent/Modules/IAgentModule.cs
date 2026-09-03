namespace TallyAgent.Modules;

/// <summary>
/// One unit of scheduled work the agent host runs on its own interval —
/// the extensibility seam the whole tool is built around. Backup Sync and
/// Backup Health Monitor are built-in; a future Gateway-based module (e.g.
/// WhatsApp delivery for Tally-only shops) plugs in the same way.
/// </summary>
public interface IAgentModule
{
    /// <summary>Stable key reported in checkin's module_status, e.g. "backup".</summary>
    string Name { get; }

    TimeSpan PollInterval { get; }

    /// <summary>
    /// Runs one poll. Must not throw for expected/recoverable conditions —
    /// return a status via <see cref="AgentContext.ReportModuleStatus"/> instead,
    /// so one module's trouble never stops the host loop or other modules.
    /// Only let unexpected exceptions escape; the host logs and continues.
    /// </summary>
    Task RunOnceAsync(AgentContext ctx, CancellationToken cancellationToken);
}
