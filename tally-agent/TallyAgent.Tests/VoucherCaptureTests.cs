using System.Net;
using System.Text;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using TallyAgent.Backend;
using TallyAgent.Modules;
using TallyAgent.Tally;

namespace TallyAgent.Tests;

/// <summary>
/// `pull_vouchers` sends one backend-built request to the gateway and parks the raw answer in R2.
/// The agent must only ever run read-only Export requests, and must treat "Tally closed / no
/// company" as retryable like the masters pull. (The R2 PUT uses its own HttpClient, so the
/// happy-path upload is covered by the masters pull, which shares it.)
/// </summary>
public class VoucherCaptureTests
{
    private const string ExportXml =
        "<ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER></ENVELOPE>";
    private const string ImportXml =
        "<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER></ENVELOPE>";

    private sealed class ScriptedTally : HttpMessageHandler
    {
        public Queue<Func<HttpResponseMessage>> Script { get; } = new();
        public int Posts { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken ct)
        {
            var body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(ct);
            if (body.Contains("List of Companies"))
                throw new HttpRequestException("connection refused"); // the standing probe
            Posts++;
            var next = Script.Count > 0 ? Script.Dequeue() : () => throw new HttpRequestException("refused");
            return next();
        }
    }

    private sealed class RecordingBackend : HttpMessageHandler
    {
        public List<(string Path, string Body)> Calls { get; } = [];

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken ct)
        {
            var body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(ct);
            Calls.Add((request.RequestUri!.AbsolutePath, body));
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent("{}", Encoding.UTF8, "application/json"),
            };
        }

        public int Count(string suffix) => Calls.Count(c => c.Path.EndsWith(suffix));
        public string? LastBody(string suffix) => Calls.LastOrDefault(c => c.Path.EndsWith(suffix)).Body;
    }

    private sealed class Rig
    {
        public ScriptedTally Tally { get; } = new();
        public RecordingBackend Backend { get; } = new();
        public TallyMastersModule Module { get; }
        public AgentContext Ctx { get; }

        public Rig()
        {
            var options = Options.Create(new AgentOptions
            {
                BackendBaseUrl = "http://backend.test",
                ShopApiKey = "k",
                TallyMasters = new TallyMastersOptions { Enabled = true, GatewayUrl = "http://tally.test:9000" },
            });
            var gateway = new TallyGatewayClient(new HttpClient(Tally), NullLogger<TallyGatewayClient>.Instance);
            var backend = new BackendClient(
                new HttpClient(Backend), options, NullLogger<BackendClient>.Instance);
            Module = new TallyMastersModule(options, gateway);
            Ctx = new AgentContext(backend, gateway, NullLoggerFactory.Instance);
        }

        public void Deliver(string exportXml) =>
            Ctx.SetPendingOutbox([new OutboxItem
            {
                Id = "ob-1",
                Module = "tally",
                Payload = new Dictionary<string, object?>
                {
                    ["job_id"] = "job-1",
                    ["action"] = "pull_vouchers",
                    ["label"] = "vouchers-apr25",
                    ["export_xml"] = exportXml,
                },
            }]);

        public Task Round() => Module.RunOnceAsync(Ctx, CancellationToken.None);
    }

    [Theory]
    [InlineData(ExportXml, true)]
    [InlineData(ImportXml, false)]
    [InlineData("<ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER><IMPORTDATA/></ENVELOPE>", false)]
    [InlineData("<ENVELOPE/>", false)]
    public void Only_a_read_only_export_is_accepted(string xml, bool expected) =>
        Assert.Equal(expected, TallyMastersModule.IsExportRequest(xml));

    [Fact]
    public async Task An_import_request_is_refused_and_never_reaches_tally()
    {
        var r = new Rig();
        r.Deliver(ImportXml);

        await r.Round();

        Assert.Equal(0, r.Tally.Posts);
        Assert.Equal(1, r.Backend.Count("/result"));
        Assert.Contains("\"error\"", r.Backend.LastBody("/result"));
        Assert.Contains("read-only Export", r.Backend.LastBody("/result"));
    }

    [Fact]
    public async Task A_capture_that_finds_tally_closed_is_retried()
    {
        var r = new Rig();
        r.Deliver(ExportXml);

        await r.Round(); // Tally closed: nothing reported as a result yet

        Assert.Equal(1, r.Tally.Posts);
        Assert.Equal(0, r.Backend.Count("/result"));
        Assert.Contains("tally_unavailable", r.Backend.LastBody("/status"));
    }

    [Fact]
    public async Task A_timeout_is_reported_as_too_big_not_replayed()
    {
        var r = new Rig();
        r.Deliver(ExportXml);
        r.Tally.Script.Enqueue(() => throw new TaskCanceledException("timed out"));

        await r.Round();
        r.Ctx.SetPendingOutbox([]);
        await r.Round();

        Assert.Equal(1, r.Tally.Posts);
        Assert.Contains("Try a shorter period", r.Backend.LastBody("/result"));
    }
}
