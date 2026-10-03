namespace TallyAgent;

/// <summary>Root config, bound from appsettings.json's "Agent" section.</summary>
public sealed class AgentOptions
{
    public const string SectionName = "Agent";

    public string ShopApiKey { get; set; } = "";
    public string BackendBaseUrl { get; set; } = "";
    // Environment variables (%LOCALAPPDATA%, %ProgramData%) are expanded - see
    // ExpandPaths. Per-user installs use %LOCALAPPDATA%\TallyAgent; the optional
    // service install rewrites these to C:\ProgramData\TallyAgent.
    public string StateDbPath { get; set; } = @"%LOCALAPPDATA%\TallyAgent\state.db";
    public string LogDirectory { get; set; } = @"%LOCALAPPDATA%\TallyAgent\logs";

    public BackupSyncOptions? BackupSync { get; set; }
    public BackupHealthMonitorOptions? BackupHealthMonitor { get; set; }
    public WhatsAppDeliveryOptions? WhatsAppDelivery { get; set; }
    public TallyMastersOptions? TallyMasters { get; set; }
    public UpdateOptions Update { get; set; } = new();

    /// <summary>Expands %VAR% in every configured path. Called once after
    /// binding (the generated appsettings.json can't know the user's profile
    /// folder, so it ships %LOCALAPPDATA%).</summary>
    public void ExpandPaths()
    {
        StateDbPath = Environment.ExpandEnvironmentVariables(StateDbPath);
        LogDirectory = Environment.ExpandEnvironmentVariables(LogDirectory);
        if (BackupSync is not null)
            BackupSync.WatchFolder = Environment.ExpandEnvironmentVariables(BackupSync.WatchFolder);
        if (TallyMasters is not null)
            TallyMasters.ExportDir = Environment.ExpandEnvironmentVariables(TallyMasters.ExportDir);
    }
}

public sealed class UpdateOptions
{
    /// <summary>Follow update offers from the backend. appsettings.Development.json
    /// turns this off so a dev `dotnet run` never swaps its own bin folder.</summary>
    public bool Enabled { get; set; } = true;
}

public sealed class BackupSyncOptions
{
    public bool Enabled { get; set; } = true;
    public string WatchFolder { get; set; } = "";

    /// <summary>Globs for one TallyPrime native backup run:
    /// <c>TDBK*</c> = the data parts (<c>.001</c>, and <c>.002</c>+ when a
    /// large backup splits, plus versioned <c>_1</c>/<c>_2</c> copies);
    /// <c>TBK*.900</c> = the manifest Restore needs alongside them.
    /// Deliberately excludes the ODBC/SQL export (<c>TSDBK*</c>), which
    /// TallyPrime cannot restore from. Overridable per-shop if a Tally
    /// build names its backups differently.</summary>
    public string[] FilePatterns { get; set; } = ["TDBK*", "TBK*.900"];
    public int PollIntervalMinutes { get; set; } = 5;
    /// <summary>How many confirmed-uploaded local backups to keep before pruning older ones.</summary>
    public int LocalRetentionCount { get; set; } = 7;
}

public sealed class BackupHealthMonitorOptions
{
    public bool Enabled { get; set; } = true;
    public int PollIntervalMinutes { get; set; } = 15;
    /// <summary>No new stable backup within this window => report backup_stalled.
    /// Tally's own backup schedule is shop-configured, not this tool's, so this
    /// must be set per shop to match it (with headroom).</summary>
    public int ExpectedIntervalHours { get; set; } = 26;
}

public sealed class WhatsAppDeliveryOptions
{
    public bool Enabled { get; set; } = false;
    public int PollIntervalMinutes { get; set; } = 5;
    public string TallyGatewayBaseUrl { get; set; } = "http://127.0.0.1:9000";
}

public sealed class TallyMastersOptions
{
    public bool Enabled { get; set; } = true;

    /// <summary>Tally Prime's HTTP XML gateway. Needs "TallyPrime acts as
    /// Server" enabled in F1 > Settings > Connectivity (port 9000 default).</summary>
    public string GatewayUrl { get; set; } = "http://127.0.0.1:9000";

    /// <summary>Optional fallback: a folder the shop's accountant exports
    /// "All Masters" XML into. Used only when the gateway is unreachable.
    /// Blank = no fallback (gateway is required).</summary>
    public string ExportDir { get; set; } = "";

    public int PollIntervalMinutes { get; set; } = 1;
}
