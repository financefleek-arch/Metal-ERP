using System.Security.Cryptography;
using System.Text;
using TallyAgent.Updates;

namespace TallyAgent.Tests;

public class ReleaseVerifierTests
{
    internal static (ECDsa Key, string PublicSpkiB64) NewKey()
    {
        var key = ECDsa.Create(ECCurve.NamedCurves.nistP256);
        return (key, Convert.ToBase64String(key.ExportSubjectPublicKeyInfo()));
    }

    internal static string Sign(ECDsa key, string version, string sha256, long size) =>
        Convert.ToBase64String(key.SignData(
            Encoding.UTF8.GetBytes($"{version}|{sha256}|{size}"), HashAlgorithmName.SHA256));

    private const string Sha = "ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12";

    [Fact]
    public void Valid_signature_verifies()
    {
        var (key, pub) = NewKey();
        var sig = Sign(key, "1.2.3", Sha, 100);
        Assert.True(ReleaseVerifier.VerifySignature("1.2.3", Sha, 100, sig, [pub]));
    }

    [Fact]
    public void Sha_case_does_not_matter()
    {
        var (key, pub) = NewKey();
        var sig = Sign(key, "1.2.3", Sha, 100);
        Assert.True(ReleaseVerifier.VerifySignature("1.2.3", Sha.ToUpperInvariant(), 100, sig, [pub]));
    }

    [Theory]
    [InlineData("1.2.4", Sha, 100)]
    [InlineData("1.2.3", "cd12cd12cd12cd12cd12cd12cd12cd12cd12cd12cd12cd12cd12cd12cd12cd12", 100)]
    [InlineData("1.2.3", Sha, 101)]
    public void Any_tampered_field_fails(string version, string sha, long size)
    {
        var (key, pub) = NewKey();
        var sig = Sign(key, "1.2.3", Sha, 100);
        Assert.False(ReleaseVerifier.VerifySignature(version, sha, size, sig, [pub]));
    }

    [Fact]
    public void Signature_from_an_untrusted_key_fails()
    {
        var (attacker, _) = NewKey();
        var (_, trusted) = NewKey();
        var sig = Sign(attacker, "1.2.3", Sha, 100);
        Assert.False(ReleaseVerifier.VerifySignature("1.2.3", Sha, 100, sig, [trusted]));
    }

    [Fact]
    public void No_trusted_keys_means_nothing_verifies()
    {
        var (key, _) = NewKey();
        var sig = Sign(key, "1.2.3", Sha, 100);
        Assert.False(ReleaseVerifier.VerifySignature("1.2.3", Sha, 100, sig, []));
    }

    [Fact]
    public void Either_of_two_trusted_keys_works_and_a_malformed_key_is_skipped()
    {
        var (oldKey, oldPub) = NewKey();
        var (_, newPub) = NewKey();
        var sig = Sign(oldKey, "1.2.3", Sha, 100);
        Assert.True(ReleaseVerifier.VerifySignature(
            "1.2.3", Sha, 100, sig, ["not-base64!!", newPub, oldPub]));
    }

    [Theory]
    [InlineData("")]
    [InlineData("%%%not base64%%%")]
    [InlineData("AAAA")]
    public void Garbage_signature_fails_without_throwing(string sig)
    {
        var (_, pub) = NewKey();
        Assert.False(ReleaseVerifier.VerifySignature("1.2.3", Sha, 100, sig, [pub]));
    }

    [Fact]
    public async Task Sha256Hex_matches_known_vector()
    {
        var path = Path.GetTempFileName();
        try
        {
            await File.WriteAllTextAsync(path, "abc");
            Assert.Equal(
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
                await ReleaseVerifier.Sha256HexAsync(path, CancellationToken.None));
        }
        finally { File.Delete(path); }
    }

    private sealed record SignerFixture(
        string version, string sha256, long size_bytes, string signature, string public_key_spki_base64);

    [Fact]
    public void Accepts_a_signature_produced_by_the_production_signer()
    {
        // Fixtures/signer-signature.json was made by api/tools/agent_release.py (the code that
        // signs real releases), so this pins the signer -> agent wire format, not just a
        // .NET sign/verify round trip. Regenerate it if the payload or signature format changes.
        var fx = System.Text.Json.JsonSerializer.Deserialize<SignerFixture>(
            File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures", "signer-signature.json")))!;

        Assert.True(ReleaseVerifier.VerifySignature(
            fx.version, fx.sha256, fx.size_bytes, fx.signature, [fx.public_key_spki_base64]));
        Assert.False(ReleaseVerifier.VerifySignature(
            fx.version, fx.sha256, fx.size_bytes + 1, fx.signature, [fx.public_key_spki_base64]));
        Assert.False(ReleaseVerifier.VerifySignature(
            "9.9.8", fx.sha256, fx.size_bytes, fx.signature, [fx.public_key_spki_base64]));
    }

    [Fact]
    public void Shipped_build_trusts_a_valid_p256_release_key()
    {
        // Fail-closed guard in both directions: an EMPTY list would make every agent refuse
        // every update, and a malformed entry would be silently skipped by the verifier.
        Assert.NotEmpty(ReleaseKeys.PublicKeysSpkiBase64);
        foreach (var spki in ReleaseKeys.PublicKeysSpkiBase64)
        {
            using var ecdsa = ECDsa.Create();
            ecdsa.ImportSubjectPublicKeyInfo(Convert.FromBase64String(spki), out _);
            Assert.Equal(256, ecdsa.KeySize);
        }
    }
}
