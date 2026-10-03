using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Logging;
using TallyAgent.Backend;

namespace TallyAgent.Updates;

public enum StartupAction
{
    /// <summary>Run normally.</summary>
    Continue,
    /// <summary>Files were swapped (or rolled back): exit now so the scheduler
    /// restarts the process from the new files.</summary>
    ExitForRestart,
}

public enum StageResult
{
    /// <summary>Verified and staged - the caller should exit now.</summary>
    Staged,
    /// <summary>Nothing to do (already on it, one already in flight, no key / updates off).</summary>
    Skipped,
    /// <summary>Permanently bad (signature, size, hash, unreadable zip, failed selftest).
    /// Reported to the backend; do not retry this version.</summary>
    Rejected,
    /// <summary>Network / disk trouble. Not reported; retry later.</summary>
    Transient,
}

public sealed class UpdateState
{
    [JsonPropertyName("version")] public string Version { get; set; } = "";
    [JsonPropertyName("previous_version")] public string PreviousVersion { get; set; } = "";
    /// <summary>"staged" = downloaded + verified, swap pending;
    /// "swapped" = new files in place, not yet proven healthy.</summary>
    [JsonPropertyName("stage")] public string Stage { get; set; } = "staged";
    [JsonPropertyName("staged_publish_dir")] public string StagedPublishDir { get; set; } = "";
    [JsonPropertyName("starts")] public int Starts { get; set; }
}

/// <summary>What the next checkin must tell the backend about a failed update.</summary>
public sealed class UpdateFailureReport
{
    [JsonPropertyName("version")] public string Version { get; set; } = "";
    [JsonPropertyName("error")] public string Error { get; set; } = "";
}

/// <summary>
/// Drives the update lifecycle across process restarts. States live in
/// <c>update-state.json</c> next to the agent's state db:
///
///   (offer) -> Stage(): verify signature, download, sha256, extract, selftest
///           -> state=staged, process exits
///   restart -> OnStartup(): state=staged  -> swap files, state=swapped, exit again
///   restart -> OnStartup(): state=swapped -> count the start; if it keeps crashing
///                           (> MaxStartsBeforeRollback) restore the old build
///           -> MarkHealthy() after ~90s alive: delete state + the old build.
///
/// "Healthy" means "the process stayed up", NOT "backend reachable" - a shop PC
/// offline during an update must not trigger a rollback of a good build.
/// </summary>
public sealed class UpdateCoordinator
{
    public const int ExitCodeForRestart = 10;
    public const int MaxStartsBeforeRollback = 4;

    private readonly string _stateDir;
    private readonly string _appDir;
    private readonly string _currentVersion;
    private readonly ILogger _log;

    private string StatePath => Path.Combine(_stateDir, "update-state.json");
    private string ReportPath => Path.Combine(_stateDir, "update-failure.json");
    private string UpdatesDir => Path.Combine(_stateDir, "updates");
    private string PrevDir => _appDir.TrimEnd('\\', '/') + ".prev";
    private string BadDir => _appDir.TrimEnd('\\', '/') + ".bad";

    public UpdateCoordinator(string stateDir, string appDir, string currentVersion, ILogger log)
    {
        _stateDir = stateDir;
        _appDir = appDir;
        _currentVersion = currentVersion;
        _log = log;
        Directory.CreateDirectory(stateDir);
    }

    // ---- startup ---------------------------------------------------------

    public StartupAction OnStartup()
    {
        var state = LoadState();
        if (state is null)
        {
            // Nothing in flight; tidy any leftovers from a finished update.
            UpdateSwapper.TryDeleteDir(PrevDir);
            UpdateSwapper.TryDeleteDir(BadDir);
            UpdateSwapper.TryDeleteDir(UpdatesDir);
            return StartupAction.Continue;
        }

        if (state.Stage == "staged")
        {
            if (state.PreviousVersion != _currentVersion || !Directory.Exists(state.StagedPublishDir))
            {
                _log.LogWarning("Discarding stale staged update {Version}", state.Version);
                ClearState();
                return StartupAction.Continue;
            }
            try
            {
                UpdateSwapper.Apply(_appDir, state.StagedPublishDir, PrevDir);
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Swap to {Version} failed; staying on {Current}", state.Version, _currentVersion);
                WriteFailure(state.Version, $"swap failed: {ex.Message}");
                ClearState();
                return StartupAction.Continue;
            }
            state.Stage = "swapped";
            state.Starts = 0;
            SaveState(state);
            _log.LogInformation("Swapped to {Version}; restarting", state.Version);
            return StartupAction.ExitForRestart;
        }

        // stage == "swapped": we are (supposed to be) the new build.
        if (state.Version != _currentVersion)
        {
            // A rollback already restored the old build, or the swap never took.
            _log.LogWarning(
                "Update state says {Wanted} but running {Current}; clearing", state.Version, _currentVersion);
            ClearState();
            return StartupAction.Continue;
        }

        state.Starts++;
        SaveState(state);
        if (state.Starts <= MaxStartsBeforeRollback) return StartupAction.Continue;

        _log.LogError("{Version} restarted {Starts}x without becoming healthy; rolling back",
            state.Version, state.Starts);
        try
        {
            UpdateSwapper.Rollback(_appDir, PrevDir, BadDir);
            WriteFailure(state.Version, $"unhealthy after {state.Starts} starts; rolled back to {state.PreviousVersion}");
            ClearState();
            return StartupAction.ExitForRestart;
        }
        catch (Exception ex)
        {
            // Nothing safe left to do; keep running whatever this is.
            _log.LogError(ex, "Rollback failed");
            WriteFailure(state.Version, $"rollback failed: {ex.Message}");
            ClearState();
            return StartupAction.Continue;
        }
    }

    /// <summary>The process has stayed up long enough: the new build is good.</summary>
    public void MarkHealthy()
    {
        var state = LoadState();
        if (state is null || state.Stage != "swapped" || state.Version != _currentVersion) return;
        _log.LogInformation("Update to {Version} confirmed healthy", state.Version);
        ClearState();
        // The old exe may still be locked by its just-exited process; OnStartup retries.
        UpdateSwapper.TryDeleteDir(PrevDir);
        UpdateSwapper.TryDeleteDir(UpdatesDir);
    }

    // ---- failure reporting ----------------------------------------------

    public UpdateFailureReport? PendingFailure()
    {
        try
        {
            return File.Exists(ReportPath)
                ? JsonSerializer.Deserialize<UpdateFailureReport>(File.ReadAllText(ReportPath))
                : null;
        }
        catch { return null; }
    }

    public void ClearFailure()
    {
        try { File.Delete(ReportPath); } catch { /* best effort */ }
    }

    private void WriteFailure(string version, string error)
    {
        try
        {
            File.WriteAllText(
                ReportPath,
                JsonSerializer.Serialize(new UpdateFailureReport { Version = version, Error = error }));
        }
        catch (Exception ex) { _log.LogWarning(ex, "Could not write the update failure report"); }
    }

    // ---- staging ---------------------------------------------------------

    /// <summary>
    /// Verifies and downloads an offered build and leaves it staged; on
    /// <see cref="StageResult.Staged"/> the caller exits and the swap happens on
    /// the next start. Permanent failures are queued for the next checkin via
    /// <see cref="PendingFailure"/>; transient ones are not.
    /// </summary>
    public async Task<StageResult> StageAsync(
        UpdateOffer offer, IReadOnlyCollection<string> trustedKeys, HttpClient http, CancellationToken ct)
    {
        if (offer.Version == _currentVersion) return StageResult.Skipped;
        if (LoadState() is not null) return StageResult.Skipped; // one update at a time

        if (trustedKeys.Count == 0)
        {
            _log.LogWarning("Update to {Version} offered but no signing key is compiled in - ignoring", offer.Version);
            return StageResult.Skipped;
        }
        if (!ReleaseVerifier.VerifySignature(
                offer.Version, offer.Sha256, offer.SizeBytes, offer.Signature, trustedKeys))
        {
            _log.LogError("Update to {Version} has an INVALID signature - ignoring", offer.Version);
            WriteFailure(offer.Version, "invalid release signature");
            return StageResult.Rejected;
        }

        var dir = Path.Combine(UpdatesDir, offer.Version);
        var zipPath = Path.Combine(dir, "agent.zip");
        try
        {
            Directory.CreateDirectory(dir);
            _log.LogInformation("Downloading agent {Version} ({Bytes} bytes)", offer.Version, offer.SizeBytes);
            await using (var resp = await http.GetStreamAsync(offer.Url, ct))
            await using (var fs = File.Create(zipPath))
            {
                await resp.CopyToAsync(fs, ct);
            }

            if (new FileInfo(zipPath).Length != offer.SizeBytes)
                throw new InvalidDataException("downloaded size does not match the signed size");
            var sha = await ReleaseVerifier.Sha256HexAsync(zipPath, ct);
            if (!string.Equals(sha, offer.Sha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("downloaded sha256 does not match the signed sha256");

            var publish = UpdateSwapper.Extract(zipPath, Path.Combine(dir, "staged"));
            await SelfTestAsync(Path.Combine(publish, "TallyAgent.exe"), offer.Version, ct);

            SaveState(new UpdateState
            {
                Version = offer.Version,
                PreviousVersion = _currentVersion,
                Stage = "staged",
                StagedPublishDir = publish,
            });
            return StageResult.Staged;
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { throw; }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException
            || (ex is IOException && ex is not InvalidDataException))
        {
            _log.LogWarning(ex, "Downloading {Version} failed (will retry later)", offer.Version);
            UpdateSwapper.TryDeleteDir(dir);
            return StageResult.Transient;
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Staging {Version} failed", offer.Version);
            WriteFailure(offer.Version, ex.Message);
            UpdateSwapper.TryDeleteDir(dir);
            return StageResult.Rejected;
        }
    }

    /// <summary>Runs the NEW exe once with --selftest so a build that cannot start
    /// on THIS machine (missing native lib, blocked by AV) is caught before the
    /// swap, not after.</summary>
    private static async Task SelfTestAsync(string exePath, string expectedVersion, CancellationToken ct)
    {
        var outFile = Path.Combine(Path.GetDirectoryName(exePath)!, "selftest.out");
        File.Delete(outFile);
        using var proc = Process.Start(new ProcessStartInfo(exePath)
        {
            ArgumentList = { "--selftest", outFile },
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = Path.GetDirectoryName(exePath)!,
        }) ?? throw new InvalidOperationException("could not start the new build for its selftest");

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        try { await proc.WaitForExitAsync(timeout.Token); }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            try { proc.Kill(true); } catch { /* already gone */ }
            throw new TimeoutException("new build's selftest did not finish in 30s");
        }

        if (proc.ExitCode != 0)
            throw new InvalidOperationException($"new build's selftest exited {proc.ExitCode}");
        var reported = File.Exists(outFile) ? File.ReadAllText(outFile).Trim() : "";
        if (reported != expectedVersion)
            throw new InvalidDataException($"new build reports version '{reported}', expected '{expectedVersion}'");
    }

    // ---- state file ------------------------------------------------------

    private UpdateState? LoadState()
    {
        try
        {
            return File.Exists(StatePath)
                ? JsonSerializer.Deserialize<UpdateState>(File.ReadAllText(StatePath))
                : null;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "update-state.json unreadable; treating as no update in flight");
            return null;
        }
    }

    private void SaveState(UpdateState s) =>
        File.WriteAllText(StatePath, JsonSerializer.Serialize(s));

    private void ClearState()
    {
        try { File.Delete(StatePath); } catch { /* best effort */ }
    }
}
