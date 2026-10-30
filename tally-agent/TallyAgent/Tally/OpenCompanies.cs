using System.Xml.Linq;

namespace TallyAgent.Tally;

/// <summary>A company TallyPrime has open, read from the gateway's Company collection.</summary>
public sealed record OpenCompany(string Name, string Guid, string? BooksFrom);

/// <summary>
/// The Company collection request and its answer. Verified live (2026-10-30): it needs no company
/// selected, lists exactly the companies loaded in that Tally process, and returns each one's name,
/// GUID and books-from date. The name is text; the GUID is what survives a rename.
/// </summary>
public static class OpenCompanies
{
    /// <summary>Read-only Export of the Company collection. The id is also how tests and logs spot it.</summary>
    public const string RequestId = "MECompanies";

    public const string Request = """
        <ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>MECompanies</ID></HEADER>
        <BODY><DESC><STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>
        <TDL><TDLMESSAGE><COLLECTION NAME="MECompanies" ISMODIFY="No"><TYPE>Company</TYPE>
        <FETCH>Name, GUID, StartingFrom, BooksFrom, MasterID</FETCH></COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>
        """;

    /// <summary>The companies in Tally's answer, or null when it is not a usable answer (an error, or not
    /// XML). An answer with no company in it is an empty list: Tally is up with nothing open.</summary>
    public static IReadOnlyList<OpenCompany>? Parse(string body)
    {
        XDocument doc;
        try
        {
            doc = XDocument.Parse(body);
        }
        catch (System.Xml.XmlException)
        {
            return null;
        }

        if (doc.Descendants("LINEERROR").Any(e => !string.IsNullOrWhiteSpace(e.Value))) return null;
        var status = doc.Descendants("HEADER").Elements("STATUS").FirstOrDefault()?.Value.Trim();
        if (status != "1") return null;

        var list = new List<OpenCompany>();
        foreach (var c in doc.Descendants("COMPANY"))
        {
            // <CMPINFO><COMPANY>0</COMPANY> is a counter, not a company: it has no GUID.
            var guid = c.Element("GUID")?.Value.Trim();
            var name = c.Element("NAME")?.Value.Trim();
            if (string.IsNullOrEmpty(name)) name = (string?)c.Attribute("NAME");
            if (string.IsNullOrEmpty(guid) || string.IsNullOrEmpty(name)) continue;
            var books = (c.Element("BOOKSFROM") ?? c.Element("STARTINGFROM"))?.Value.Trim();
            list.Add(new OpenCompany(name.Trim(), guid, ToIsoDate(books)));
        }
        return list;
    }

    private static string? ToIsoDate(string? tallyDate) =>
        DateTime.TryParseExact(tallyDate, "yyyyMMdd", null, System.Globalization.DateTimeStyles.None, out var d)
            ? d.ToString("yyyy-MM-dd")
            : null;
}
