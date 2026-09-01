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

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
