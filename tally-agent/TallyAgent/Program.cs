using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Options;
using Serilog;
using TallyAgent;
using TallyAgent.Backend;
using TallyAgent.Modules;
using TallyAgent.State;
using TallyAgent.Tally;
using TallyAgent.Updates;

// `TallyAgent.exe --selftest <outfile>`: used by the updater to prove a freshly
// downloaded build can start on THIS machine (native sqlite loads, TLS stack
// initialises) before it is swapped in. Writes its version to <outfile>.
if (args.Length >= 1 && args[0] == "--selftest")
    return SelfTest(args.Length > 1 ? args[1] : null);

// One agent per machine. Two copies (two RDP sessions, or a service plus a
// scheduled task) would both push the same vouchers. A mutex owned by another
// user's session is "access denied" - equally "already running".
Mutex? singleton = null;
try
{
    // TALLYAGENT_INSTANCE lets a second, isolated copy run on a dev box that
    // already has the real agent (tests / e2e); unset in production.
    var instance = Environment.GetEnvironmentVariable("TALLYAGENT_INSTANCE") ?? "TallyAgentSingleton";
    singleton = new Mutex(initiallyOwned: true, @"Global\" + instance, out var createdNew);
    if (!createdNew) return 0;
}
catch (UnauthorizedAccessException)
{
    return 0;
}

var builder = Host.CreateApplicationBuilder(args);

builder.Services.Configure<AgentOptions>(builder.Configuration.GetSection(AgentOptions.SectionName));
builder.Services.PostConfigure<AgentOptions>(o => o.ExpandPaths());

// Serilog to a rolling file under the configured LogDirectory, so a shop
// visit / remote session can diagnose "why didn't this file upload"
// without needing the central admin API.
var agentOptions = builder.Configuration.GetSection(AgentOptions.SectionName).Get<AgentOptions>()
    ?? new AgentOptions();
agentOptions.ExpandPaths();
Directory.CreateDirectory(agentOptions.LogDirectory);
Log.Logger = new LoggerConfiguration()
    .MinimumLevel.Information()
    .WriteTo.File(
        Path.Combine(agentOptions.LogDirectory, "tally-agent-.log"),
        rollingInterval: RollingInterval.Day,
        retainedFileCountLimit: 30)
    .CreateLogger();
builder.Services.AddSerilog();

builder.Services.AddSingleton<AgentStateStore>();
builder.Services.AddSingleton(sp => new UpdateCoordinator(
    stateDir: Path.GetDirectoryName(sp.GetRequiredService<IOptions<AgentOptions>>().Value.StateDbPath)!,
    appDir: AppContext.BaseDirectory.TrimEnd('\\', '/'),
    currentVersion: AgentVersion.Current,
    log: sp.GetRequiredService<ILoggerFactory>().CreateLogger("Update")));
builder.Services.AddHttpClient<BackendClient>();
builder.Services.AddHttpClient<TallyGatewayClient>();

builder.Services.AddSingleton<IAgentModule, BackupSyncModule>();
builder.Services.AddSingleton<IAgentModule, BackupHealthMonitorModule>();
builder.Services.AddSingleton<IAgentModule, WhatsAppDeliveryModule>();
builder.Services.AddSingleton<IAgentModule, TallyMastersModule>();

builder.Services.AddHostedService<TallyAgentService>();

// Runs at boot with no login session when installed as a Windows Service
// (see install.ps1); falls back to a normal console app under `dotnet run`.
builder.Services.AddWindowsService(o => o.ServiceName = "TallyAgent");

var host = builder.Build();
host.Run();
GC.KeepAlive(singleton);
return 0;

static int SelfTest(string? outFile)
{
    try
    {
        using (var conn = new SqliteConnection("Data Source=:memory:"))
        {
            conn.Open();
            using var cmd = conn.CreateCommand();
            cmd.CommandText = "SELECT 1";
            cmd.ExecuteScalar();
        }
        using (new HttpClient()) { }
        if (outFile is null) Console.WriteLine(AgentVersion.Current);
        else File.WriteAllText(outFile, AgentVersion.Current);
        return 0;
    }
    catch (Exception ex)
    {
        try { if (outFile is not null) File.WriteAllText(outFile + ".err", ex.ToString()); } catch { }
        return 1;
    }
}
