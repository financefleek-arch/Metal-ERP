using System.Net;
using System.Text;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using TallyAgent.Backend;
using TallyAgent.Modules;
using TallyAgent.Tally;

namespace TallyAgent.Tests;

/// <summary>
/// The backend hands a Tally job to the agent ONCE, so the agent has to keep a job that found
/// Tally "not ready" and retry it itself - but only when the request provably never reached
/// Tally. These tests drive the real module against a scripted Tally and a recording backend.
/// </summary>
public class TallyMastersRetryTests
{
    private const string OkXml =
        "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ERRORS>0</ERRORS>"
        + "<EXCEPTIONS>0</EXCEPTIONS></IMPORTRESULT></DATA></BODY></ENVELOPE>";

    private sealed class ScriptedTally : HttpMessageHandler
    {
        /// <summary>What to do with the next VOUCHER post (probes are answered separately).</summary>
        public Queue<Func<HttpResponseMessage>> Script { get; } = new();
        public int VoucherPosts { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken ct)
        {
            var body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(ct);
            if (body.Contains("List of Companies"))
            {
                // the standing reachability probe: just say "no" like a closed Tally would
                throw new HttpRequestException("connection refused");
            }
            VoucherPosts++;
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
        public DateTimeOffset Now { get; set; } = new(2026, 10, 4, 10, 0, 0, TimeSpan.Zero);

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
            Module = new TallyMastersModule(options, gateway) { Clock = () => Now };
            Ctx = new AgentContext(backend, gateway, NullLoggerFactory.Instance);
        }

        /// <summary>The checkin that delivers the job (once). Later checkins hand over nothing.</summary>
        public void Deliver(string jobId = "job-1") =>
            Ctx.SetPendingOutbox([new OutboxItem
            {
                Id = "ob-1",
                Module = "tally",
                Payload = new Dictionary<string, object?>
                {
                    ["job_id"] = jobId,
                    ["action"] = "push_items",
                    ["voucher_xml"] = "<ENVELOPE><MARK/></ENVELOPE>",
                },
            }]);

        public void NextCheckinDeliversNothing() => Ctx.SetPendingOutbox([]);

        public Task Round() => Module.RunOnceAsync(Ctx, CancellationToken.None);
    }

    [Fact]
    public async Task A_job_that_finds_tally_closed_is_retried_until_tally_is_back()
    {
        var r = new Rig();
        r.Deliver();

        await r.Round(); // Tally closed
        r.NextCheckinDeliversNothing(); // the backend will not redeliver: it already marked it sent
        await r.Round(); // still closed
        Assert.Equal(2, r.Tally.VoucherPosts);
        Assert.Equal(0, r.Backend.Count("/result"));
        Assert.Equal(2, r.Backend.Count("/status"));
        Assert.Contains("tally_unavailable", r.Backend.LastBody("/status"));

        r.Tally.Script.Enqueue(() => new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(OkXml) });
        await r.Round(); // Tally opened

        Assert.Equal(3, r.Tally.VoucherPosts);
        Assert.Equal(1, r.Backend.Count("/result"));
        Assert.Contains("\"ok\"", r.Backend.LastBody("/result"));
        Assert.Contains("IMPORTRESULT", r.Backend.LastBody("/result"));

        await r.Round(); // finished: nothing is sent again
        await r.Round();
        Assert.Equal(3, r.Tally.VoucherPosts);
        Assert.Equal(1, r.Backend.Count("/result"));
    }

    [Fact]
    public async Task No_company_open_is_also_retried()
    {
        var r = new Rig();
        r.Deliver();
        r.Tally.Script.Enqueue(() => new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("<RESPONSE>Could not find Company ''</RESPONSE>"),
        });
        await r.Round();
        Assert.Contains("no_company_loaded", r.Backend.LastBody("/status"));
        Assert.Equal(0, r.Backend.Count("/result"));

        r.NextCheckinDeliversNothing();
        r.Tally.Script.Enqueue(() => new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(OkXml) });
        await r.Round();
        Assert.Equal(1, r.Backend.Count("/result"));
    }

    [Fact]
    public async Task A_timeout_is_reported_as_an_error_and_never_replayed()
    {
        // Tally might have processed it: replaying could create duplicates.
        var r = new Rig();
        r.Deliver();
        r.Tally.Script.Enqueue(() => throw new TaskCanceledException("timed out"));

        await r.Round();
        r.NextCheckinDeliversNothing();
        await r.Round();
        await r.Round();

        Assert.Equal(1, r.Tally.VoucherPosts);
        Assert.Equal(1, r.Backend.Count("/result"));
        Assert.Contains("\"error\"", r.Backend.LastBody("/result"));
        Assert.Contains("did not answer in time", r.Backend.LastBody("/result"));
    }

    [Fact]
    public async Task An_http_error_from_tally_is_reported_and_never_replayed()
    {
        var r = new Rig();
        r.Deliver();
        r.Tally.Script.Enqueue(() => new HttpResponseMessage(HttpStatusCode.InternalServerError));

        await r.Round();
        r.NextCheckinDeliversNothing();
        await r.Round();

        Assert.Equal(1, r.Tally.VoucherPosts);
        Assert.Equal(1, r.Backend.Count("/result"));
        Assert.Contains("500", r.Backend.LastBody("/result"));
    }

    [Fact]
    public async Task The_agent_gives_up_inside_the_backends_ten_minute_window()
    {
        Assert.True(TallyMastersModule.RetryWindow < TimeSpan.FromMinutes(10));

        var r = new Rig();
        r.Deliver();
        await r.Round(); // t = 0, Tally closed
        r.NextCheckinDeliversNothing();
        r.Now += TimeSpan.FromMinutes(5);
        await r.Round(); // still inside the window: tried again
        Assert.Equal(2, r.Tally.VoucherPosts);

        r.Now += TimeSpan.FromMinutes(4); // 9 min since first seen: past the agent's window
        await r.Round();
        Assert.Equal(2, r.Tally.VoucherPosts); // dropped; the backend cancels it so the shop can resend
    }
}
