using Microsoft.Extensions.Logging;
using TallyAgent.Backend;
using TallyAgent.Tally;

namespace TallyAgent.Modules;

/// <summary>
/// Shared dependencies handed to every module on each poll: the backend
/// client (checkin/upload/outbox), the shared Tally Gateway client for
/// modules that need it, and a place to report this poll's status so the
/// next checkin's module_status reflects it accurately.
/// </summary>
public sealed class AgentContext(
    BackendClient backend,
    TallyGatewayClient tallyGateway,
    ILoggerFactory loggerFactory)
{
    public BackendClient Backend { get; } = backend;
    public TallyGatewayClient TallyGateway { get; } = tallyGateway;

    private readonly Dictionary<string, string> _moduleStatus = new();
    private string? _lastError;

    // The outbox from the most recent checkin response. The host calls
    // checkin AFTER the module round, so a module sees the previous round's
    // outbox — one poll of latency, acceptable at the 1-minute checkin
    // cadence (and the tally module's not-ready states retry anyway).
    private IReadOnlyList<OutboxItem> _pendingOutbox = Array.Empty<OutboxItem>();

    public IReadOnlyList<OutboxItem> PendingOutbox => _pendingOutbox;

    public void SetPendingOutbox(IReadOnlyList<OutboxItem> outbox) =>
        _pendingOutbox = outbox ?? Array.Empty<OutboxItem>();

    public ILogger CreateLogger(string category) => loggerFactory.CreateLogger(category);

    /// <summary>Records this module's outcome for the next checkin call.</summary>
    public void ReportModuleStatus(string moduleName, string status) =>
        _moduleStatus[moduleName] = status;

    public void ReportError(string message) => _lastError = message;

    public IReadOnlyDictionary<string, string> ModuleStatusSnapshot => _moduleStatus;

    public string? LastErrorSnapshot => _lastError;

    // Second checkin signal: can this agent reach TallyPrime's HTTP gateway
    // right now? Independent of module_status/backend reachability above.
    // Null until a module has probed at least once this run.
    public bool? TallyReachable { get; private set; }
    public string? TallyReason { get; private set; }

    /// <summary>Records this poll's Tally-gateway reachability for the next
    /// checkin. `reason` is one of connected|refused|no_company|unknown.</summary>
    public void SetTallyReachable(bool reachable, string reason)
    {
        TallyReachable = reachable;
        TallyReason = reason;
    }
}
