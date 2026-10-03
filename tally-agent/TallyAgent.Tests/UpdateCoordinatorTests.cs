using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using Microsoft.Extensions.Logging.Abstractions;
using TallyAgent.Backend;
using TallyAgent.Updates;

namespace TallyAgent.Tests;

public class UpdateCoordinatorTests
{
    private sealed class Rig : IDisposable
    {
        public TempDir T = new();
        public string App => T.Sub("app");
        public string StateDir => T.Sub("state");
        public UpdateCoordinator Coord;

        public Rig(string currentVersion)
        {
            Directory.CreateDirectory(App);
            File.WriteAllText(Path.Combine(App, "TallyAgent.exe"), "old-exe-" + currentVersion);
            File.WriteAllText(Path.Combine(App, "appsettings.json"), "{shop-key}");
            Coord = Make(currentVersion);
        }

        public UpdateCoordinator Make(string version) =>
            new(StateDir, App, version, NullLogger.Instance);

        public void WriteState(UpdateState s) =>
            File.WriteAllText(Path.Combine(StateDir, "update-state.json"),
                System.Text.Json.JsonSerializer.Serialize(s));

        public bool HasState => File.Exists(Path.Combine(StateDir, "update-state.json"));

        public string StageNewBuild(string exeContent)
        {
            var staged = T.Sub("staged-publish");
            Directory.CreateDirectory(staged);
            File.WriteAllText(Path.Combine(staged, "TallyAgent.exe"), exeContent);
            return staged;
        }

        public void Dispose() => T.Dispose();
    }

    [Fact]
    public void Staged_state_swaps_files_then_asks_for_a_restart()
    {
        using var r = new Rig("1.0.0");
        var staged = r.StageNewBuild("new-exe-1.1.0");
        r.WriteState(new UpdateState
        {
            Version = "1.1.0", PreviousVersion = "1.0.0", Stage = "staged", StagedPublishDir = staged,
        });

        Assert.Equal(StartupAction.ExitForRestart, r.Coord.OnStartup());

        Assert.Equal("new-exe-1.1.0", File.ReadAllText(Path.Combine(r.App, "TallyAgent.exe")));
        Assert.Equal("{shop-key}", File.ReadAllText(Path.Combine(r.App, "appsettings.json")));
    }

    [Fact]
    public void New_build_that_stays_up_is_confirmed_and_the_old_one_removed()
    {
        using var r = new Rig("1.0.0");
        r.WriteState(new UpdateState
        {
            Version = "1.1.0", PreviousVersion = "1.0.0", Stage = "swapped", Starts = 0,
        });
        Directory.CreateDirectory(r.App + ".prev");

        var coord = r.Make("1.1.0"); // the restarted process
        Assert.Equal(StartupAction.Continue, coord.OnStartup());
        Assert.True(r.HasState);

        coord.MarkHealthy();

        Assert.False(r.HasState);
        Assert.False(Directory.Exists(r.App + ".prev"));
    }

    [Fact]
    public void Crash_looping_new_build_is_rolled_back_and_reported()
    {
        using var r = new Rig("1.0.0");
        var staged = r.StageNewBuild("new-exe-1.1.0");
        r.WriteState(new UpdateState
        {
            Version = "1.1.0", PreviousVersion = "1.0.0", Stage = "staged", StagedPublishDir = staged,
        });
        r.Coord.OnStartup(); // swap

        var coord = r.Make("1.1.0");
        for (var i = 0; i < UpdateCoordinator.MaxStartsBeforeRollback; i++)
            Assert.Equal(StartupAction.Continue, coord.OnStartup()); // keeps "crashing" (never MarkHealthy)

        Assert.Equal(StartupAction.ExitForRestart, coord.OnStartup()); // one too many

        Assert.Equal("old-exe-1.0.0", File.ReadAllText(Path.Combine(r.App, "TallyAgent.exe")));
        Assert.False(r.HasState);
        var report = coord.PendingFailure();
        Assert.NotNull(report);
        Assert.Equal("1.1.0", report!.Version);
        Assert.Contains("rolled back", report.Error);

        coord.ClearFailure();
        Assert.Null(coord.PendingFailure());
    }

    [Fact]
    public void Stale_staged_update_is_discarded()
    {
        using var r = new Rig("1.0.0");
        r.WriteState(new UpdateState
        {
            Version = "1.1.0", PreviousVersion = "0.9.0", Stage = "staged", StagedPublishDir = r.T.Sub("gone"),
        });
        Assert.Equal(StartupAction.Continue, r.Coord.OnStartup());
        Assert.False(r.HasState);
        Assert.Equal("old-exe-1.0.0", File.ReadAllText(Path.Combine(r.App, "TallyAgent.exe")));
    }

    // ---- StageAsync ------------------------------------------------------

    private sealed class FakeHandler(Func<HttpResponseMessage> respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) =>
            Task.FromResult(respond());
    }

    private static HttpClient Http(byte[] body) =>
        new(new FakeHandler(() => new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(body) }));

    private static (UpdateOffer Offer, string PubKey, byte[] Zip) SignedOffer(
        string version, byte[] zip, ECDsa? signer = null)
    {
        var (key, pub) = ReleaseVerifierTests.NewKey();
        var sha = Convert.ToHexString(SHA256.HashData(zip)).ToLowerInvariant();
        var offer = new UpdateOffer
        {
            Version = version, Url = "https://r2.test/a.zip", Sha256 = sha, SizeBytes = zip.Length,
            Signature = ReleaseVerifierTests.Sign(signer ?? key, version, sha, zip.Length),
        };
        return (offer, pub, zip);
    }

    private static byte[] ZipOf(string exeContent)
    {
        using var ms = new MemoryStream();
        using (var z = new ZipArchive(ms, ZipArchiveMode.Create, leaveOpen: true))
        {
            using var w = new StreamWriter(z.CreateEntry("publish/TallyAgent.exe").Open());
            w.Write(exeContent);
        }
        return ms.ToArray();
    }

    [Fact]
    public async Task Invalid_signature_is_rejected_and_reported_without_downloading()
    {
        using var r = new Rig("1.0.0");
        var (attacker, _) = ReleaseVerifierTests.NewKey();
        var (offer, pub, _) = SignedOffer("1.1.0", ZipOf("x"), signer: attacker);
        // trusted key is the legit one, signature is from the attacker
        var called = false;
        var http = new HttpClient(new FakeHandler(() => { called = true; return new HttpResponseMessage(HttpStatusCode.OK); }));

        var res = await r.Coord.StageAsync(offer, [pub], http, CancellationToken.None);

        Assert.Equal(StageResult.Rejected, res);
        Assert.False(called);
        Assert.Equal("1.1.0", r.Coord.PendingFailure()!.Version);
    }

    [Fact]
    public async Task No_trusted_keys_skips_silently()
    {
        using var r = new Rig("1.0.0");
        var (offer, _, zip) = SignedOffer("1.1.0", ZipOf("x"));
        Assert.Equal(StageResult.Skipped, await r.Coord.StageAsync(offer, [], Http(zip), CancellationToken.None));
        Assert.Null(r.Coord.PendingFailure());
    }

    [Fact]
    public async Task Offer_for_the_running_version_is_skipped()
    {
        using var r = new Rig("1.1.0");
        var (offer, pub, zip) = SignedOffer("1.1.0", ZipOf("x"));
        Assert.Equal(StageResult.Skipped, await r.Coord.StageAsync(offer, [pub], Http(zip), CancellationToken.None));
    }

    [Fact]
    public async Task Download_that_differs_from_the_signed_hash_is_rejected()
    {
        using var r = new Rig("1.0.0");
        var (offer, pub, zip) = SignedOffer("1.1.0", ZipOf("good"));
        var tampered = (byte[])zip.Clone();
        tampered[^1] ^= 0xFF; // same size, different bytes

        var res = await r.Coord.StageAsync(offer, [pub], Http(tampered), CancellationToken.None);

        Assert.Equal(StageResult.Rejected, res);
        Assert.False(r.HasState);
        Assert.Contains("sha256", r.Coord.PendingFailure()!.Error);
    }

    [Fact]
    public async Task Network_failure_is_transient_and_not_reported()
    {
        using var r = new Rig("1.0.0");
        var (offer, pub, _) = SignedOffer("1.1.0", ZipOf("x"));
        var http = new HttpClient(new FakeHandler(() => throw new HttpRequestException("boom")));

        Assert.Equal(StageResult.Transient, await r.Coord.StageAsync(offer, [pub], http, CancellationToken.None));
        Assert.Null(r.Coord.PendingFailure());
        Assert.False(r.HasState);
    }

    [Fact]
    public async Task Build_that_cannot_run_its_selftest_is_rejected()
    {
        using var r = new Rig("1.0.0");
        // A zip whose "TallyAgent.exe" is not a runnable program.
        var (offer, pub, zip) = SignedOffer("1.1.0", ZipOf("this is not an executable"));

        var res = await r.Coord.StageAsync(offer, [pub], Http(zip), CancellationToken.None);

        Assert.Equal(StageResult.Rejected, res);
        Assert.False(r.HasState);
        Assert.NotNull(r.Coord.PendingFailure());
    }

    [Fact]
    public async Task Real_build_passes_selftest_and_is_staged()
    {
        // Packages the REAL TallyAgent build next to this test assembly as the
        // "release", so the --selftest path (native sqlite, TLS stack) is
        // exercised for real, not mocked.
        var buildDir = Path.GetDirectoryName(typeof(AgentVersion).Assembly.Location)!;
        if (!File.Exists(Path.Combine(buildDir, "TallyAgent.exe")))
            return; // apphost not produced on this platform

        using var r = new Rig("9.9.9");
        using var ms = new MemoryStream();
        using (var z = new ZipArchive(ms, ZipArchiveMode.Create, leaveOpen: true))
        {
            // Recursive: the native e_sqlite3.dll lives under runtimes/win-x64/native.
            foreach (var f in Directory.EnumerateFiles(buildDir, "*", SearchOption.AllDirectories))
            {
                var rel = Path.GetRelativePath(buildDir, f).Replace('\\', '/');
                if (rel.StartsWith("TallyAgent.Tests") || rel.EndsWith(".pdb")) continue;
                z.CreateEntryFromFile(f, "publish/" + rel);
            }
        }
        // A local build reports 0.0.0 as its version.
        var (offer, pub, zip) = SignedOffer("0.0.0", ms.ToArray());

        var res = await r.Coord.StageAsync(offer, [pub], Http(zip), CancellationToken.None);

        Assert.Equal(StageResult.Staged, res);
        Assert.True(r.HasState);
        Assert.Null(r.Coord.PendingFailure());
    }
}
