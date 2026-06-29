"""Application settings (env-driven, prefix ``TM_``)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TM_", env_file=".env", extra="ignore")

    app_name: str = "tallymigration-backend"
    environment: str = "dev"  # dev | prod
    log_level: str = "INFO"
    log_json: bool = True

    # ---- upload / data guards ----
    max_upload_bytes: int = 25 * 1024 * 1024  # 25 MB
    max_rows: int = 200_000
    max_columns: int = 1_000

    # ---- persistence ----
    # dev default = local SQLite; prod = postgresql+psycopg://user:pass@host/db
    database_url: str = "sqlite:///./tallymigration.db"

    # ---- auth ----
    # >=32 bytes for HMAC-SHA256. This is a DEV placeholder — OVERRIDE in prod via TM_JWT_SECRET.
    jwt_secret: str = "dev-only-insecure-secret-change-me-via-TM_JWT_SECRET-env"
    jwt_expire_minutes: int = 60 * 24

    # ---- object storage (uploads + generated XML) ----
    storage_backend: str = "local"  # local | memory | s3
    storage_local_dir: str = "./storage"
    s3_bucket: str = "tallymigration"
    s3_endpoint: str = ""  # e.g. http://localhost:9000 for MinIO
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # ---- task queue (taskiq) ----
    broker_url: str = ""  # empty => in-process InMemoryBroker; prod: redis://host:6379
    broker_result_url: str = ""  # defaults to broker_url

    # ---- working-set cache (parsed DataFrames etc. held per process) ----
    max_jobs: int = 500  # LRU-evicted from the in-process working cache

    # ---- CORS ----
    cors_origins: list[str] = ["*"]

    # ---- dev-only DIRECT push to a local Tally gateway (bypasses the bridge) ----
    # In production the bridge relays XML; this is only for same-machine dev/E2E testing.
    direct_tally_push: bool = False
    tally_url: str = "http://127.0.0.1:9000"
    tally_timeout_seconds: float = 60.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
