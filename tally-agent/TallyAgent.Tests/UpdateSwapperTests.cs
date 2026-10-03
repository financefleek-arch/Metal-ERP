using System.IO.Compression;
using TallyAgent.Updates;

namespace TallyAgent.Tests;

public sealed class TempDir : IDisposable
{
    public string Path { get; } =
        System.IO.Path.Combine(System.IO.Path.GetTempPath(), "ta-test-" + Guid.NewGuid().ToString("N")[..10]);

    public TempDir() => Directory.CreateDirectory(Path);

    public string Sub(string name) => System.IO.Path.Combine(Path, name);

    public void Dispose()
    {
        try { Directory.Delete(Path, true); } catch { /* best effort */ }
    }
}

public class UpdateSwapperTests
{
    private static void Write(string dir, string rel, string content)
    {
        var p = Path.Combine(dir, rel);
        Directory.CreateDirectory(Path.GetDirectoryName(p)!);
        File.WriteAllText(p, content);
    }

    private static string Read(string dir, string rel) => File.ReadAllText(Path.Combine(dir, rel));

    private static string MakeZip(TempDir t, params (string Name, string Content)[] entries)
    {
        var zipPath = t.Sub("r.zip");
        using var zip = ZipFile.Open(zipPath, ZipArchiveMode.Create);
        foreach (var (name, content) in entries)
        {
            using var w = new StreamWriter(zip.CreateEntry(name).Open());
            w.Write(content);
        }
        return zipPath;
    }

    [Fact]
    public void Extract_returns_publish_dir()
    {
        using var t = new TempDir();
        var zip = MakeZip(t, ("publish/TallyAgent.exe", "exe"), ("install.ps1", "#"), ("publish/sub/a.dll", "dll"));
        var publish = UpdateSwapper.Extract(zip, t.Sub("staged"));
        Assert.Equal("exe", Read(publish, "TallyAgent.exe"));
        Assert.Equal("dll", Read(publish, Path.Combine("sub", "a.dll")));
    }

    [Fact]
    public void Extract_rejects_zip_slip()
    {
        using var t = new TempDir();
        var zip = MakeZip(t, ("publish/TallyAgent.exe", "exe"), ("../evil.txt", "x"));
        Assert.Throws<InvalidDataException>(() => UpdateSwapper.Extract(zip, t.Sub("staged")));
        Assert.False(File.Exists(Path.Combine(t.Path, "evil.txt")));
    }

    [Fact]
    public void Extract_requires_the_exe()
    {
        using var t = new TempDir();
        var zip = MakeZip(t, ("publish/other.dll", "x"));
        Assert.Throws<InvalidDataException>(() => UpdateSwapper.Extract(zip, t.Sub("staged")));
    }

    [Fact]
    public void Apply_replaces_build_files_but_keeps_appsettings()
    {
        using var t = new TempDir();
        var app = t.Sub("app"); var staged = t.Sub("staged"); var prev = t.Sub("app.prev");
        Write(app, "TallyAgent.exe", "old-exe");
        Write(app, "old-only.dll", "old-dll");
        Write(app, "appsettings.json", "{shop-key}");
        Write(staged, "TallyAgent.exe", "new-exe");
        Write(staged, "new-only.dll", "new-dll");
        Write(staged, "appsettings.json", "{TEMPLATE-must-not-win}");

        UpdateSwapper.Apply(app, staged, prev);

        Assert.Equal("new-exe", Read(app, "TallyAgent.exe"));
        Assert.Equal("new-dll", Read(app, "new-only.dll"));
        Assert.False(File.Exists(Path.Combine(app, "old-only.dll")));
        Assert.Equal("{shop-key}", Read(app, "appsettings.json"));
        Assert.Equal("old-exe", Read(prev, "TallyAgent.exe"));
        Assert.Equal("old-dll", Read(prev, "old-only.dll"));
    }

    [Fact]
    public void Apply_failure_restores_the_original_build()
    {
        using var t = new TempDir();
        var app = t.Sub("app"); var staged = t.Sub("staged"); var prev = t.Sub("app.prev");
        Write(app, "TallyAgent.exe", "old-exe");
        Write(app, "x/inner.dll", "old-inner");   // leaves an empty dir "x" after the move...
        Write(staged, "TallyAgent.exe", "new-exe");
        Write(staged, "x", "a FILE where the app has a directory"); // ...so this copy fails

        Assert.ThrowsAny<Exception>(() => UpdateSwapper.Apply(app, staged, prev));

        Assert.Equal("old-exe", Read(app, "TallyAgent.exe"));
        Assert.Equal("old-inner", Read(app, Path.Combine("x", "inner.dll")));
    }

    [Fact]
    public void Rollback_puts_the_previous_build_back()
    {
        using var t = new TempDir();
        var app = t.Sub("app"); var staged = t.Sub("staged");
        var prev = t.Sub("app.prev"); var bad = t.Sub("app.bad");
        Write(app, "TallyAgent.exe", "old-exe");
        Write(app, "appsettings.json", "{shop-key}");
        Write(staged, "TallyAgent.exe", "new-exe");
        UpdateSwapper.Apply(app, staged, prev);

        UpdateSwapper.Rollback(app, prev, bad);

        Assert.Equal("old-exe", Read(app, "TallyAgent.exe"));
        Assert.Equal("{shop-key}", Read(app, "appsettings.json"));
        Assert.Equal("new-exe", Read(bad, "TallyAgent.exe"));
    }

    [Fact]
    public void Rollback_without_a_previous_build_throws()
    {
        using var t = new TempDir();
        Directory.CreateDirectory(t.Sub("app"));
        Assert.Throws<DirectoryNotFoundException>(
            () => UpdateSwapper.Rollback(t.Sub("app"), t.Sub("app.prev"), t.Sub("app.bad")));
    }
}
