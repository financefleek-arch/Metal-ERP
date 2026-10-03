using System.Security.Cryptography;
using System.Text;

namespace TallyAgent.Updates;

/// <summary>
/// Public keys the agent trusts to sign releases (base64 SubjectPublicKeyInfo,
/// ECDSA P-256). The private key is a Vault KV secret (secret/metalerp/core#agent_signing_key,
/// delivered to metalerp-api as AGENT_SIGNING_KEY) and is used only by
/// api/tools/agent_release.py. Print the public key on the VPS with
/// `docker exec metalerp-api python -m tools.agent_release public-keys` and paste the
/// line here. Two entries let the key be rotated through a normal release: ship a build
/// trusting both (signed with the current key), wait for every shop to update, then
/// activate the new key. See fleek-infra/vault/RUNBOOK.md, "Agent release signing key".
///
/// EMPTY = auto-update refuses every offer (fail closed) - an unconfigured
/// build can never be pushed code by anyone.
/// </summary>
public static class ReleaseKeys
{
    public static readonly string[] PublicKeysSpkiBase64 =
    [
        // Release signing key, created 2026-10-03 (Vault: secret/metalerp/core#agent_signing_key).
        "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEv7GB1Z92FrLydwfeuW8lr1WEeYRwQkepXl02Ox8d7N6xHD4TqgKPq2RIpFjvrYDiL55DplHmpCKxXx4wT5kNgg==",
    ];
}

/// <summary>
/// Verifies a release offer. The backend only relays the manifest; trust comes
/// from an ECDSA P-256 / SHA-256 signature, made in CI, over the UTF-8 string
/// "{version}|{sha256}|{size_bytes}" (IEEE P1363 r||s, base64) - so a
/// compromised API or bucket cannot push an unsigned or altered build.
/// </summary>
public static class ReleaseVerifier
{
    public static string SignedPayload(string version, string sha256, long sizeBytes) =>
        $"{version}|{sha256.ToLowerInvariant()}|{sizeBytes}";

    public static bool VerifySignature(
        string version, string sha256, long sizeBytes, string signatureBase64,
        IEnumerable<string> trustedPublicKeysSpkiBase64)
    {
        byte[] sig;
        try { sig = Convert.FromBase64String(signatureBase64); }
        catch (FormatException) { return false; }

        var data = Encoding.UTF8.GetBytes(SignedPayload(version, sha256, sizeBytes));
        foreach (var keyB64 in trustedPublicKeysSpkiBase64)
        {
            try
            {
                using var ecdsa = ECDsa.Create();
                ecdsa.ImportSubjectPublicKeyInfo(Convert.FromBase64String(keyB64), out _);
                if (ecdsa.VerifyData(data, sig, HashAlgorithmName.SHA256)) return true;
            }
            catch (Exception ex) when (ex is CryptographicException or FormatException)
            {
                // A malformed trusted key must not mask a good one after it.
            }
        }
        return false;
    }

    public static async Task<string> Sha256HexAsync(string path, CancellationToken ct)
    {
        await using var fs = File.OpenRead(path);
        var hash = await SHA256.HashDataAsync(fs, ct);
        return Convert.ToHexString(hash).ToLowerInvariant();
    }
}
