using System.Net;
using System.Text;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using TallyAgent.Backend;
using TallyAgent.Modules;
using TallyAgent.Tally;

namespace TallyAgent.Tests;

/// <summary>
/// S0.3: before a job touches Tally the agent checks that the company it names is the one that is open -
/// by GUID when the backend knows it, by name otherwise - and stops with plain words when it is not.
/// Company lists below are what the live dev Tally returned (2026-10-30).
/// </summary>
public class CompanyCheckTests
{
    private const string FleekGuid = "167b31a2-6486-4396-8ee8-7fe38496052d";
    private const string TestGuid = "67baeb49-5454-418e-aad2-4ae28441e6d1";

    private const string OkXml =
        "<ENVELOPE><BODY><DATA><IMPORTRESULT><CREATED>1</CREATED><ERRORS>0</ERRORS>"
        + "<EXCEPTIONS>0</EXCEPTIONS></IMPORTRESULT></DATA></BODY></ENVELOPE>";

    private static string CompanyList(params (string Name, string Guid)[] companies)
    {
        var body = string.Concat(companies.Select(c =>
            $"<COMPANY NAME=\"{c.Name}\" RESERVEDNAME=\"\"><STARTINGFROM TYPE=\"Date\">20260401</STARTINGFROM>" +
            $"<BOOKSFROM TYPE=\"Date\">20260401</BOOKSFROM><NAME TYPE=\"String\">{c.Name}</NAME>" +
            $"<GUID TYPE=\"String\">{c.Guid}</GUID><MASTERID TYPE=\"Number\"> 29</MASTERID></COMPANY>"));
        return "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY>" +
               "<DESC><CMPINFO><COMPANY>0</COMPANY><LEDGER>27</LEDGER></CMPINFO></DESC>" +
               $"<DATA><COLLECTION>{body}</COLLECTION></DATA></BODY></ENVELOPE>";
    }

    private static readonly OpenCompany Fleek = new("Fleek", FleekGuid, "2026-04-01");
    private static readonly OpenCompany FleekTest = new("Fleek Test", TestGuid, "2026-04-01");

    // ---- the list -----------------------------------------------------------------------------------

    [Fact]
    public void Parser_reads_name_guid_and_books_from_and_skips_the_counter()
    {
        var got = OpenCompanies.Parse(CompanyList(("Fleek", FleekGuid), ("Fleek Test", TestGuid)));
        Assert.Equal([Fleek, FleekTest], got);
    }

    [Fact]
    public void Parser_gives_an_empty_list_when_nothing_is_open_and_null_when_it_is_not_an_answer()
    {
        Assert.Empty(OpenCompanies.Parse(CompanyList())!);
        Assert.Null(OpenCompanies.Parse("not xml"));
        Assert.Null(OpenCompanies.Parse(
            "<ENVELOPE><HEADER><STATUS>0</STATUS></HEADER><BODY><DATA><LINEERROR>nope</LINEERROR></DATA></BODY></ENVELOPE>"));
    }

    [Fact]
    public void The_request_is_a_read_only_export_that_names_no_company()
    {
        Assert.True(TallyMastersModule.IsExportRequest(OpenCompanies.Request));
        Assert.DoesNotContain("SVCURRENTCOMPANY", OpenCompanies.Request);
    }

    // ---- the decision -------------------------------------------------------------------------------

    [Fact]
    public void A_job_that_names_no_company_is_never_stopped()
    {
        Assert.Equal(CompanyVerdict.Proceed, CompanyCheck.Decide(null, null, [Fleek]).Verdict);
        Assert.Equal(CompanyVerdict.Proceed, CompanyCheck.Decide("  ", "", [Fleek]).Verdict);
    }

    [Fact]
    public void When_the_list_cannot_be_read_or_is_empty_tally_gets_to_say_so()
    {
        Assert.Equal(CompanyVerdict.Proceed, CompanyCheck.Decide("Fleek", FleekGuid, null).Verdict);
        Assert.Equal(CompanyVerdict.Proceed, CompanyCheck.Decide("Fleek", FleekGuid, []).Verdict);
    }

    [Fact]
    public void Same_guid_and_name_proceeds_and_the_name_is_case_blind()
    {
        Assert.Equal(CompanyVerdict.Proceed, CompanyCheck.Decide("fleek", FleekGuid, [Fleek, FleekTest]).Verdict);
    }

    [Fact]
    public void Name_only_matches_by_name_when_no_guid_is_known()
    {
        Assert.Equal(CompanyVerdict.Proceed, CompanyCheck.Decide("Fleek Test", null, [Fleek, FleekTest]).Verdict);
    }

    [Fact]
    public void Same_guid_under_another_name_is_a_rename_and_stops()
    {
        var d = CompanyCheck.Decide("Fleek", FleekGuid, [new OpenCompany("Fleek Traders", FleekGuid, null)]);
        Assert.Equal(CompanyVerdict.Renamed, d.Verdict);
        Assert.Equal("company_renamed", d.ErrorCode);
        Assert.Contains("Fleek Traders", d.Message);
        Assert.Contains("Nothing was sent", d.Message);
    }

    [Fact]
    public void Same_name_with_another_guid_is_the_wrong_company_and_stops()
    {
        var impostor = new OpenCompany("Fleek", "00000000-0000-0000-0000-000000000000", null);
        var d = CompanyCheck.Decide("Fleek", FleekGuid, [impostor]);
        Assert.Equal(CompanyVerdict.WrongCompany, d.Verdict);
        Assert.Equal("wrong_company", d.ErrorCode);
    }

    [Fact]
    public void A_company_that_is_not_open_waits_and_says_what_is_open()
    {
        var d = CompanyCheck.Decide("Shah Traders", "11111111-1111-1111-1111-111111111111", [Fleek, FleekTest]);
        Assert.Equal(CompanyVerdict.NotOpen, d.Verdict);
        Assert.Null(d.ErrorCode);
        Assert.Contains("\"Fleek\"", d.Message);
        Assert.Contains("\"Fleek Test\"", d.Message);
        Assert.Equal(CompanyVerdict.NotOpen, CompanyCheck.Decide("Shah Traders", null, [Fleek]).Verdict);
    }

    // ---- the module, against a fake gateway ---------------------------------------------------------

    private sealed class FakeTally : HttpMessageHandler
    {
        public string Companies { get; set; } = CompanyList(("Fleek", FleekGuid), ("Fleek Test", TestGuid));
        public int VoucherPosts { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken ct)
        {
            var body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(ct);
            var reply = body.Contains("MECompanies")
                ? Companies
                : body.Contains("List of Companies")
                    ? "<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER></ENVELOPE>"
                    : Posted(ref body);
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(reply) };
        }

        private string Posted(ref string _)
        {
            VoucherPosts++;
            return OkXml;
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
        public FakeTally Tally { get; } = new();
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

        public void Deliver(string? companyName, string? companyGuid)
        {
            var payload = new Dictionary<string, object?>
            {
                ["job_id"] = "job-1",
                ["action"] = "push_sales",
                ["voucher_xml"] = "<ENVELOPE><MARK/></ENVELOPE>",
            };
            if (companyName is not null) payload["company_name"] = companyName;
            if (companyGuid is not null) payload["company_guid"] = companyGuid;
            Ctx.SetPendingOutbox([new OutboxItem { Id = "ob-1", Module = "tally", Payload = payload }]);
        }

        public Task Round() => Module.RunOnceAsync(Ctx, CancellationToken.None);
    }

    [Fact]
    public async Task The_right_company_gets_the_voucher()
    {
        var r = new Rig();
        r.Deliver("Fleek Test", TestGuid);
        await r.Round();

        Assert.Equal(1, r.Tally.VoucherPosts);
        Assert.Contains("\"ok\"", r.Backend.LastBody("/result"));
    }

    [Fact]
    public async Task A_renamed_company_stops_with_a_code_and_nothing_is_posted()
    {
        var r = new Rig();
        r.Tally.Companies = CompanyList(("Fleek Traders", FleekGuid));
        r.Deliver("Fleek", FleekGuid);
        await r.Round();

        Assert.Equal(0, r.Tally.VoucherPosts);
        var sent = r.Backend.LastBody("/result")!;
        Assert.Contains("\"error\"", sent);
        Assert.Contains("company_renamed", sent);
        Assert.Contains("Fleek Traders", sent);

        r.Ctx.SetPendingOutbox([]); // the backend hands a job over once
        await r.Round(); // stopped for good: not retried
        Assert.Equal(1, r.Backend.Count("/result"));
        Assert.Equal(0, r.Tally.VoucherPosts);
    }

    [Fact]
    public async Task A_different_company_with_our_name_stops_with_a_code_and_nothing_is_posted()
    {
        var r = new Rig();
        r.Tally.Companies = CompanyList(("Fleek", "00000000-0000-0000-0000-000000000000"));
        r.Deliver("Fleek", FleekGuid);
        await r.Round();

        Assert.Equal(0, r.Tally.VoucherPosts);
        Assert.Contains("wrong_company", r.Backend.LastBody("/result"));
    }

    [Fact]
    public async Task A_company_not_open_waits_and_goes_through_once_it_is_opened()
    {
        var r = new Rig();
        r.Tally.Companies = CompanyList(("Fleek", FleekGuid));
        r.Deliver("Fleek Test", TestGuid);

        await r.Round();
        Assert.Equal(0, r.Tally.VoucherPosts);
        Assert.Equal(0, r.Backend.Count("/result"));
        Assert.Contains("company_not_open", r.Backend.LastBody("/status"));

        r.Ctx.SetPendingOutbox([]); // the backend hands a job over once
        await r.Round();
        Assert.Equal(0, r.Tally.VoucherPosts);

        r.Tally.Companies = CompanyList(("Fleek", FleekGuid), ("Fleek Test", TestGuid)); // the owner opens it
        await r.Round();
        Assert.Equal(1, r.Tally.VoucherPosts);
        Assert.Contains("\"ok\"", r.Backend.LastBody("/result"));
    }

    [Fact]
    public async Task A_job_that_names_no_company_behaves_as_before()
    {
        var r = new Rig();
        r.Deliver(null, null);
        await r.Round();

        Assert.Equal(1, r.Tally.VoucherPosts);
        Assert.Contains("\"ok\"", r.Backend.LastBody("/result"));
    }

    [Fact]
    public async Task The_round_reads_the_open_companies_for_the_checkin()
    {
        var r = new Rig();
        r.Ctx.SetPendingOutbox([]);
        await r.Round();

        Assert.Equal([Fleek, FleekTest], r.Ctx.OpenCompanies);
    }
}
