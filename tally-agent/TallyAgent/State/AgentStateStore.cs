using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Options;

namespace TallyAgent.State;

/// <summary>
/// Local, on-disk record of which backup files this agent has already
/// processed (by filename + size + mtime) and their upload outcome —
/// survives service restarts, so a restart never re-uploads or re-prunes
/// incorrectly. Also holds the shared local outbox for Gateway-dependent
/// modules (see Modules/WhatsAppDeliveryModule) queued while Tally is closed.
/// </summary>
public sealed class AgentStateStore
{
    private readonly string _connectionString;

    public AgentStateStore(IOptions<AgentOptions> options)
    {
        var path = options.Value.StateDbPath;
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        _connectionString = $"Data Source={path}";
        Initialize();
    }

    private SqliteConnection Open()
    {
        var conn = new SqliteConnection(_connectionString);
        conn.Open();
        return conn;
    }

    private void Initialize()
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            CREATE TABLE IF NOT EXISTS processed_backup (
                file_key TEXT PRIMARY KEY,     -- filename|size|mtime
                filename TEXT NOT NULL,
                full_path TEXT NOT NULL,
                status TEXT NOT NULL,          -- pending_upload | confirmed | failed
                upload_id TEXT NULL,
                first_seen_utc TEXT NOT NULL,
                confirmed_utc TEXT NULL
            );

            CREATE TABLE IF NOT EXISTS local_outbox (
                id TEXT PRIMARY KEY,
                module TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,          -- queued | sent | failed
                created_utc TEXT NOT NULL,
                sent_utc TEXT NULL
            );
            """;
        cmd.ExecuteNonQuery();
    }

    /// <summary>True if this exact file (by key) already has a confirmed or
    /// in-flight upload — a "failed" row is deliberately NOT known, so the
    /// next poll retries it.</summary>
    public bool IsKnown(string fileKey)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT 1 FROM processed_backup
            WHERE file_key = $key AND status IN ('pending_upload', 'confirmed')
            """;
        cmd.Parameters.AddWithValue("$key", fileKey);
        return cmd.ExecuteScalar() is not null;
    }

    public void RecordPendingUpload(string fileKey, string filename, string fullPath, string uploadId)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO processed_backup (file_key, filename, full_path, status, upload_id, first_seen_utc)
            VALUES ($key, $name, $path, 'pending_upload', $uploadId, $now)
            ON CONFLICT(file_key) DO UPDATE SET status = 'pending_upload', upload_id = $uploadId;
            """;
        cmd.Parameters.AddWithValue("$key", fileKey);
        cmd.Parameters.AddWithValue("$name", filename);
        cmd.Parameters.AddWithValue("$path", fullPath);
        cmd.Parameters.AddWithValue("$uploadId", uploadId);
        cmd.Parameters.AddWithValue("$now", DateTime.UtcNow.ToString("O"));
        cmd.ExecuteNonQuery();
    }

    public void MarkConfirmed(string fileKey)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            UPDATE processed_backup SET status = 'confirmed', confirmed_utc = $now
            WHERE file_key = $key;
            """;
        cmd.Parameters.AddWithValue("$key", fileKey);
        cmd.Parameters.AddWithValue("$now", DateTime.UtcNow.ToString("O"));
        cmd.ExecuteNonQuery();
    }

    public void MarkFailed(string fileKey)
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "UPDATE processed_backup SET status = 'failed' WHERE file_key = $key;";
        cmd.Parameters.AddWithValue("$key", fileKey);
        cmd.ExecuteNonQuery();
    }

    /// <summary>Confirmed uploads, most recent first — used by retention pruning.
    /// Only ever returns files this tool itself confirmed as uploaded.</summary>
    public List<(string FileKey, string FullPath, DateTime ConfirmedUtc)> ConfirmedUploadsNewestFirst()
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT file_key, full_path, confirmed_utc FROM processed_backup
            WHERE status = 'confirmed'
            ORDER BY confirmed_utc DESC;
            """;
        var result = new List<(string, string, DateTime)>();
        using var reader = cmd.ExecuteReader();
        while (reader.Read())
        {
            result.Add((reader.GetString(0), reader.GetString(1), DateTime.Parse(reader.GetString(2))));
        }
        return result;
    }

    public DateTime? MostRecentConfirmedUtc()
    {
        using var conn = Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT MAX(confirmed_utc) FROM processed_backup WHERE status = 'confirmed';";
        var value = cmd.ExecuteScalar();
        return value is null or DBNull ? null : DateTime.Parse((string)value);
    }
}
