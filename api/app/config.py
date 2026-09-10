"""Application settings, loaded from environment variables.

No .env file is read in production — the deploy passes real values via
docker-compose.yml's `environment:` block (see infra repo). A local .env
is honoured only for developer convenience.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://metalerp:metalerp@localhost:5432/metalerp"
    base_url: str = "http://localhost:8000"

    # Auth
    jwt_secret: str = "dev-insecure-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60 * 12

    # PDF output directory (bind-mounted volume in prod)
    pdf_dir: str = "/data/pdfs"

    # Inward Bill Import (ext_inward_import). Source PDFs + generated Tally XML
    # live under here (a bind-mounted volume in prod, same "swap to S3 later"
    # contract as pdf_dir).
    inward_dir: str = "/data/inward"

    # LLM line-disambiguation (X3) + vision extraction (X7). Off by default —
    # fuzzy-only line matching until real bills show the miss rate. When true,
    # anthropic_api_key must be set.
    llm_enabled: bool = False
    anthropic_api_key: str | None = None

    # Optional integrations
    # Brevo transactional-email HTTP API key — shared across the fleek
    # stack (secret/brevo/api). Used from Stage 1+ for invoice email.
    brevo_api_key: str | None = None
    sentry_dsn: str | None = None

    # WhatsApp Business (Meta Cloud API). We reuse the existing "FleekWA" Meta
    # app exactly as fleek-backend does: one process-wide System User token
    # (`whatsapp_api_key`) that already has access to every number shared into
    # the Business Manager. Per-firm rows in `tenant_whatsapp_config` only
    # carry the `phone_number_id` that selects which number a firm sends from
    # — no per-firm token, so nothing secret is stored at rest.
    #
    # `whatsapp_app_secret` holds the Meta App Secret (FleekWA). Metal ERP
    # uses it for the inbound status webhook only: as the HMAC key for the
    # POST `X-Hub-Signature-256` check AND as the GET-handshake verify-token
    # (one secret, both jobs — see routers/whatsapp.py). fleek-backend uses
    # this same App Secret for its own webhook HMAC check
    # (routes/intelligence.py), sourced from the same Vault field
    # `fleek-backend/core#whatsapp_app_secret`. Outbound sends need only the
    # token; a missing app secret degrades receipts, it doesn't block sends.
    whatsapp_api_key: str | None = None
    whatsapp_app_secret: str | None = None
    whatsapp_api_version: str = "v25.0"  # matches fleek-backend's tested value

    @property
    def whatsapp_configured(self) -> bool:
        """Outbound sends need only the System User token."""
        return bool(self.whatsapp_api_key)

    # Tally companion agent — cloud backup sync. A dedicated Cloudflare R2
    # bucket/token, deliberately separate from the infra/postgres backup
    # container's own R2_* env vars (different bucket/account — this is a
    # different sold product, not Fleek's internal DB backups).
    tally_r2_endpoint_url: str | None = None
    tally_r2_access_key_id: str | None = None
    tally_r2_secret_access_key: str | None = None
    tally_r2_bucket: str | None = None

    @property
    def tally_r2_configured(self) -> bool:
        return bool(
            self.tally_r2_endpoint_url
            and self.tally_r2_access_key_id
            and self.tally_r2_secret_access_key
            and self.tally_r2_bucket
        )

    # Cloud retention for the companion agent's backup uploads: keep at most
    # this many confirmed backups per shop, pruning the oldest (object + row)
    # after each new confirm. A firm may override via
    # `BackupShop.backup_retention_count`; NULL there falls back to this.
    tally_backup_retention_count: int = 30

    # Pre-published tally-agent Windows build (dotnet publish -r win-x64
    # --self-contained output), dropped here by a manual/CI step. The admin
    # "download installer" action zips this directory + a generated
    # per-shop appsettings.json + install.ps1 on the fly — one build serves
    # every shop, only the small config file differs per download.
    tally_agent_build_dir: str = "/data/tally-agent-build"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
