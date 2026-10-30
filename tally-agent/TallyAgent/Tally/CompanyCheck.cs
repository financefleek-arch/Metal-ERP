namespace TallyAgent.Tally;

/// <summary>What to do with a job, given the company it names and the companies Tally has open.</summary>
public enum CompanyVerdict
{
    /// <summary>Send it: the named company is open, or there is nothing to check against.</summary>
    Proceed,
    /// <summary>Keep the job and retry: the company is not open yet (the owner may be about to open it).</summary>
    NotOpen,
    /// <summary>Stop: a company with this name is open, but it is not the one that was linked.</summary>
    WrongCompany,
    /// <summary>Stop: the linked company is open, under a different name.</summary>
    Renamed,
}

public sealed record CompanyDecision(CompanyVerdict Verdict, string? Message = null, string? ErrorCode = null);

/// <summary>
/// The company guard that runs before a job touches Tally. Pure: it compares identities and never reads a
/// voucher. By GUID when the backend knows it (a rename, or another company with the same name, is then
/// caught), by name otherwise. Anything it cannot judge falls through to <see cref="CompanyVerdict.Proceed"/>
/// so the agent behaves exactly as it did before: Tally's own refusal still protects the books.
/// </summary>
public static class CompanyCheck
{
    public const string NotOpenStatus = "company_not_open";

    public static CompanyDecision Decide(string? name, string? guid, IReadOnlyList<OpenCompany>? open)
    {
        name = name?.Trim();
        guid = guid?.Trim();
        if (string.IsNullOrEmpty(name) && string.IsNullOrEmpty(guid)) return Proceed; // old behaviour
        if (open is null || open.Count == 0) return Proceed; // could not read, or nothing open: Tally says so

        var byName = string.IsNullOrEmpty(name)
            ? null
            : open.FirstOrDefault(c => string.Equals(c.Name, name, StringComparison.OrdinalIgnoreCase));

        if (!string.IsNullOrEmpty(guid))
        {
            var byGuid = open.FirstOrDefault(c => string.Equals(c.Guid, guid, StringComparison.OrdinalIgnoreCase));
            if (byGuid is not null)
            {
                if (string.IsNullOrEmpty(name) || string.Equals(byGuid.Name, name, StringComparison.OrdinalIgnoreCase))
                    return Proceed;
                return new CompanyDecision(
                    CompanyVerdict.Renamed,
                    $"The company linked to this firm is now called \"{byGuid.Name}\" in Tally, but this firm still " +
                    $"says \"{name}\". Nothing was sent. Update the name on the Tally page, then send again.",
                    "company_renamed");
            }
            if (byName is not null)
                return new CompanyDecision(
                    CompanyVerdict.WrongCompany,
                    $"Tally has a company called \"{byName.Name}\" open, but it is not the one linked to this firm " +
                    "(its Tally id is different). Nothing was sent. Check that it is the right books on the " +
                    "Tally page, then send again.",
                    "wrong_company");
        }
        else if (byName is not null)
        {
            return Proceed;
        }

        return new CompanyDecision(CompanyVerdict.NotOpen, $"\"{name}\" is not open in Tally. Open now: {Names(open)}.");
    }

    private static CompanyDecision Proceed { get; } = new(CompanyVerdict.Proceed);

    private static string Names(IReadOnlyList<OpenCompany> open) =>
        string.Join(", ", open.Select(c => $"\"{c.Name}\""));
}
