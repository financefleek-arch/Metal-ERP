using System.IO.Compression;

namespace TallyAgent.Updates;

/// <summary>
/// File-level half of an update, kept free of hosting/network so it can be unit
/// tested against temp dirs. Windows will not let a running exe be overwritten
/// or deleted, but it CAN be renamed/moved - so the swap moves every installed
/// file aside (to <c>prevDir</c>), copies the new build in, and the process then
/// exits and is restarted by Task Scheduler / the SCM. No helper process, no
/// PowerShell. <c>appsettings.json</c> (the shop key) is never touched.
/// </summary>
public static class UpdateSwapper
{
    /// <summary>Files that belong to this shop, not to the build.</summary>
    private static readonly HashSet<string> Keep =
        new(StringComparer.OrdinalIgnoreCase) { "appsettings.json" };

    /// <summary>Unzips an agent release zip into <paramref name="stagingDir"/>,
    /// refusing any entry that would land outside it (zip-slip). Returns the
    /// <c>publish</c> directory inside it.</summary>
    public static string Extract(string zipPath, string stagingDir)
    {
        if (Directory.Exists(stagingDir)) Directory.Delete(stagingDir, true);
        Directory.CreateDirectory(stagingDir);
        var root = Path.GetFullPath(stagingDir) + Path.DirectorySeparatorChar;

        using var zip = ZipFile.OpenRead(zipPath);
        foreach (var entry in zip.Entries)
        {
            if (string.IsNullOrEmpty(entry.Name)) continue; // directory entry
            var dest = Path.GetFullPath(Path.Combine(stagingDir, entry.FullName));
            if (!dest.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException($"Zip entry escapes the staging dir: {entry.FullName}");
            Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
            entry.ExtractToFile(dest, overwrite: true);
        }

        var publish = Path.Combine(stagingDir, "publish");
        if (!File.Exists(Path.Combine(publish, "TallyAgent.exe")))
            throw new InvalidDataException("Release zip has no publish/TallyAgent.exe.");
        return publish;
    }

    /// <summary>Moves the installed build aside and copies the staged one in.
    /// On any failure the original build is put back and the exception rethrown.</summary>
    public static void Apply(string appDir, string stagedPublishDir, string prevDir)
    {
        if (Directory.Exists(prevDir)) Directory.Delete(prevDir, true);
        Directory.CreateDirectory(prevDir);

        var moved = new List<string>();
        var copied = new List<string>();
        try
        {
            foreach (var rel in RelativeFiles(appDir))
            {
                if (Keep.Contains(rel)) continue;
                var dest = Path.Combine(prevDir, rel);
                Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
                File.Move(Path.Combine(appDir, rel), dest);
                moved.Add(rel);
            }
            foreach (var rel in RelativeFiles(stagedPublishDir))
            {
                if (Keep.Contains(rel)) continue;
                var dest = Path.Combine(appDir, rel);
                Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
                File.Copy(Path.Combine(stagedPublishDir, rel), dest, overwrite: true);
                copied.Add(rel);
            }
        }
        catch
        {
            foreach (var rel in copied) TryDelete(Path.Combine(appDir, rel));
            foreach (var rel in moved) TryMove(Path.Combine(prevDir, rel), Path.Combine(appDir, rel));
            throw;
        }
    }

    /// <summary>Puts the previous build back: whatever is installed now (except
    /// appsettings.json) is moved to <paramref name="badDir"/>, then the saved
    /// files return. The running exe may be one of the files moved - fine.</summary>
    public static void Rollback(string appDir, string prevDir, string badDir)
    {
        if (!Directory.Exists(prevDir))
            throw new DirectoryNotFoundException($"No previous build at {prevDir} to roll back to.");
        if (Directory.Exists(badDir)) Directory.Delete(badDir, true);
        Directory.CreateDirectory(badDir);

        foreach (var rel in RelativeFiles(appDir))
        {
            if (Keep.Contains(rel)) continue;
            var dest = Path.Combine(badDir, rel);
            Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
            File.Move(Path.Combine(appDir, rel), dest);
        }
        foreach (var rel in RelativeFiles(prevDir))
        {
            var dest = Path.Combine(appDir, rel);
            Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
            File.Move(Path.Combine(prevDir, rel), dest, overwrite: true);
        }
    }

    public static void TryDeleteDir(string dir)
    {
        try { if (Directory.Exists(dir)) Directory.Delete(dir, true); }
        catch { /* a file still locked by a just-exited process; next start retries */ }
    }

    private static IEnumerable<string> RelativeFiles(string dir) =>
        Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories)
            .Select(f => Path.GetRelativePath(dir, f))
            .ToList();

    private static void TryDelete(string path)
    {
        try { File.Delete(path); } catch { /* best effort during recovery */ }
    }

    private static void TryMove(string from, string to)
    {
        try { File.Move(from, to, overwrite: true); } catch { /* best effort during recovery */ }
    }
}
