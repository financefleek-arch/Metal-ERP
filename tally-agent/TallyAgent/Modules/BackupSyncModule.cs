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
/// </summary>
public sealed class BackupSyncModule(
    IOptions<AgentOptions> options,
    AgentStateStore state) : IAgentModule
{
    private readonly BackupSyncOptions? _opts = options.Value.BackupSync;

    // filename -> (size, firstSeenAtThisSize) from the previous poll, to
    // detect two-consecutive-polls-same-size without a third table.
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

    private async Task ProcessLandedFilesAsync(AgentContext ctx, ILogger log, CancellationToken ct)
    {
        var candidates = Directory.EnumerateFiles(_opts!.WatchFolder, _opts.FilePattern, SearchOption.TopDirectoryOnly);

        foreach (var path in candidates)
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
                    await UploadAsync(ctx, log, path, info, fileKey, ct);
                }
            }

            _lastSeenSize[path] = currentSize;
        }
    }

    private async Task UploadAsync(
        AgentContext ctx, ILogger log, string path, FileInfo info, string fileKey, CancellationToken ct)
    {
        log.LogInformation("Uploading landed backup {File} ({Size} bytes)", info.Name, info.Length);
        var req = await ctx.Backend.RequestUploadAsync(info.Name, info.Length, ct);
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
