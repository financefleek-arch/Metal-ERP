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

    public ILogger CreateLogger(string category) => loggerFactory.CreateLogger(category);

    /// <summary>Records this module's outcome for the next checkin call.</summary>
    public void ReportModuleStatus(string moduleName, string status) =>
        _moduleStatus[moduleName] = status;

    public void ReportError(string message) => _lastError = message;

    public IReadOnlyDictionary<string, string> ModuleStatusSnapshot => _moduleStatus;

    public string? LastErrorSnapshot => _lastError;
}
