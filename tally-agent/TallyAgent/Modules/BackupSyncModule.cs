using System.Text.RegularExpressions;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using TallyAgent.State;

namespace TallyAgent.Modules;

/// <summary>
/// Module #1. Watches <see cref="BackupSyncOptions.WatchFolder"/> for Tally's
/// scheduled backup output, uploads a landed (size-stable) file to cloud
/// storage via a backend-issued pre-signed URL, then prunes old confirmed
/// local copies beyond <see cref="BackupSyncOptions.LocalRetentionCount"/>.
///
/// "Landed" = size unchanged across two consecutive polls, so a file Tally
/// is still writing is never uploaded half-finished. Never deletes or
/// touches a file that hasn't been confirmed uploaded.
///
/// One Tally backup run is several files — a <c>TBK…900</c> manifest plus
/// one or more <c>TDBK…001/.002…</c> data parts. Every file of a run is
/// tagged with the same <c>set_id</c> (derived from the shared filename
/// stem) so the backend's cloud retention prunes whole sets, never a
/// half set that can't be restored.
/// </summary>
public sealed partial class BackupSyncModule(
    IOptions<AgentOptions> options,
    AgentStateStore state) : IAgentModule
{
    private readonly BackupSyncOptions? _opts = options.Value.BackupSync;

    // filename -> last-seen size from the previous poll, to detect
    // two-consecutive-polls-same-size without a third table.
    private readonly Dictionary<string, long> _lastSeenSize = new();

    public string Name => "backup";
    public TimeSpan PollInterval => TimeSpan.FromMinutes(_opts?.PollIntervalMinutes ?? 5);

    public async Task RunOnceAsync(AgentContext ctx, CancellationToken ct)
    {
        var log = ctx.CreateLogger("BackupSync");
        if (_opts is null || !_opts.Enabled)
        {
            ctx.ReportModuleStatus(Name, "disabled");
            return;
        }
        if (!Directory.Exists(_opts.WatchFolder))
        {
            log.LogWarning("Watch folder does not exist: {Folder}", _opts.WatchFolder);
            ctx.ReportModuleStatus(Name, "watch_folder_missing");
            ctx.ReportError($"Backup watch folder not found: {_opts.WatchFolder}");
            return;
        }

        try
        {
            await ProcessLandedFilesAsync(ctx, log, ct);
            PruneOldLocalCopies(log);
            ctx.ReportModuleStatus(Name, "ok");
        }
        catch (Exception ex)
        {
            log.LogError(ex, "backup sync poll failed");
            ctx.ReportModuleStatus(Name, "error");
            ctx.ReportError($"Backup sync error: {ex.Message}");
        }
    }

    private IEnumerable<string> EnumerateCandidates()
    {
        var patterns = _opts!.FilePatterns is { Length: > 0 }
            ? _opts.FilePatterns
            : new[] { "TDBK*", "TBK*.900" };

        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var pattern in patterns)
        {
            foreach (var path in Directory.EnumerateFiles(
                         _opts!.WatchFolder, pattern, SearchOption.TopDirectoryOnly))
            {
                // A file can match two globs (e.g. TBK….900 under both
                // "TBK*" and "TBK*.900") — upload it once.
                if (seen.Add(path))
                {
                    yield return path;
                }
            }
        }
    }

    private async Task ProcessLandedFilesAsync(AgentContext ctx, ILogger log, CancellationToken ct)
    {
        foreach (var path in EnumerateCandidates())
        {
            ct.ThrowIfCancellationRequested();
            var info = new FileInfo(path);
            var currentSize = info.Length;

            if (_lastSeenSize.TryGetValue(path, out var previousSize) && previousSize == currentSize)
            {
                // Stable across two polls — landed.
                var fileKey = $"{info.Name}|{currentSize}|{info.LastWriteTimeUtc:O}";
                if (!state.IsKnown(fileKey))
                {
                    await UploadAsync(ctx, log, path, info, fileKey, SetIdFor(info.Name), ct);
                }
            }

            _lastSeenSize[path] = currentSize;
        }
    }

    /// <summary>
    /// Stable identifier shared by every file of one Tally backup run.
    /// The manifest is TBK(release)_(company)[_(version)].900 and the data
    /// parts are TDBK(release)_(company)[_(version)].001/.002... - same run,
    /// so both normalise to tbk:(release)_(company)_v(version) (version
    /// defaults to 0). A versioned run (..._2.001) is a distinct set.
    /// Deterministic and state-free, so a straggler part uploaded on a
    /// later poll still joins its set. Only TBK / TDBK names reach here
    /// (see EnumerateCandidates); anything else becomes its own singleton.
    /// </summary>
    internal static string SetIdFor(string filename)
    {
        var m = BackupNameRegex().Match(filename);
        if (!m.Success)
        {
            return $"file:{filename}";
        }
        var release = m.Groups["release"].Value;
        var company = m.Groups["company"].Value;
        var version = m.Groups["version"].Success ? m.Groups["version"].Value : "0";
        return $"tbk:{release}_{company}_v{version}";
    }

    // T, optional D, then BK — matches TBK… and TDBK…, but not TSDBK…
    // (which never reaches here anyway; the SQL export is filtered out by
    // FilePatterns).
    [GeneratedRegex(
        @"^TD?BK(?<release>\d+)_(?<company>\d+)(?:_(?<version>\d+))?\.\w+$",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex BackupNameRegex();

    private async Task UploadAsync(
        AgentContext ctx, ILogger log, string path, FileInfo info,
        string fileKey, string setId, CancellationToken ct)
    {
        log.LogInformation(
            "Uploading landed backup {File} ({Size} bytes, set {SetId})",
            info.Name, info.Length, setId);
        var req = await ctx.Backend.RequestUploadAsync(info.Name, info.Length, ct, setId);
        if (req is null)
        {
            log.LogWarning("upload-request returned no response for {File}", info.Name);
            return;
        }

        state.RecordPendingUpload(fileKey, info.Name, path, req.UploadId);

        try
        {
            await ctx.Backend.PutFileAsync(req.PutUrl, path, ct);
            await ctx.Backend.ConfirmUploadAsync(req.UploadId, "confirmed", ct);
            state.MarkConfirmed(fileKey);
            log.LogInformation("Uploaded and confirmed {File}", info.Name);
        }
        catch (Exception ex)
        {
            log.LogWarning(ex, "Upload of {File} failed — will retry next poll", info.Name);
            state.MarkFailed(fileKey);
            // Failed rows are retried: IsKnown() only guards against
            // re-processing a *confirmed* upload's exact file_key, and a
            // failed row's status lets a future poll re-attempt it (the
            // fileKey itself is unchanged since the file hasn't changed).
        }
    }

    private void PruneOldLocalCopies(ILogger log)
    {
        var confirmed = state.ConfirmedUploadsNewestFirst();
        var toPrune = confirmed.Skip(_opts!.LocalRetentionCount);
        foreach (var (_, fullPath, _) in toPrune)
        {
            try
            {
                if (File.Exists(fullPath))
                {
                    File.Delete(fullPath);
                    log.LogInformation("Pruned confirmed-uploaded local backup {Path}", fullPath);
                }
            }
            catch (Exception ex)
            {
                // Never fatal — local disk cleanup is best-effort, the cloud
                // copy is already safe.
                log.LogWarning(ex, "Could not prune {Path}", fullPath);
            }
        }
    }
}
