using System.Reflection;
using System.Runtime.InteropServices;

namespace TallyAgent.Updates;

/// <summary>This build's version (set by CI via -p:Version=X.Y.Z; 0.0.0 for a
/// local build) and the OS string reported at checkin.</summary>
public static class AgentVersion
{
    public static string Current { get; } = Resolve();

    /// <summary>e.g. "Microsoft Windows NT 10.0.22631.0 / X64".</summary>
    public static string OsDescription { get; } =
        $"{Environment.OSVersion.VersionString} / {RuntimeInformation.OSArchitecture}";

    private static string Resolve()
    {
        var info = Assembly.GetEntryAssembly()?
            .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion;
        if (string.IsNullOrWhiteSpace(info)) return "0.0.0";
        var plus = info.IndexOf('+');
        return plus >= 0 ? info[..plus] : info;
    }
}
