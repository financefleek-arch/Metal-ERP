namespace TallyAgent;

/// <summary>Root config, bound from appsettings.json's "Agent" section.</summary>
public sealed class AgentOptions
{
    public const string SectionName = "Agent";

    public string ShopApiKey { get; set; } = "";
    public string BackendBaseUrl { get; set; } = "";
    public string StateDbPath { get; set; } = @"C:\ProgramData\TallyAgent\state.db";
    public string LogDirectory { get; set; } = @"C:\ProgramData\TallyAgent\logs";

    public BackupSyncOptions? BackupSync { get; set; }
    public BackupHealthMonitorOptions? BackupHealthMonitor { get; set; }
    public WhatsAppDeliveryOptions? WhatsAppDelivery { get; set; }
}

public sealed class BackupSyncOptions
{
    public bool Enabled { get; set; } = true;
    public string WatchFolder { get; set; } = "";
    /// <summary>Glob, e.g. "*.001" or "*" — Tally's exact backup output naming
    /// varies by version/config, so this is left to per-shop configuration.</summary>
    public string FilePattern { get; set; } = "*";
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
    public string TallyGatewayBaseUrl { get; set; } = "http://localhost:9000";
}
