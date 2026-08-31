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

    # Optional integrations
    smtp_url: str | None = None
    sentry_dsn: str | None = None

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
