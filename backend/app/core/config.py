"""Application settings (env-driven, prefix ``TM_``)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
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
    # auth_mode selects which credentials the API accepts:
    #   legacy   — only first-party HS256 tokens from /auth/login|signup (current behaviour)
    #   hybrid   — legacy HS256 *and* Keycloak OIDC tokens (migration window)
    #   keycloak — only Keycloak OIDC tokens; password endpoints are disabled
    auth_mode: str = "legacy"  # legacy | hybrid | keycloak

    # >=32 bytes for HMAC-SHA256. This is a DEV placeholder — OVERRIDE in prod via TM_JWT_SECRET.
    jwt_secret: str = "dev-only-insecure-secret-change-me-via-TM_JWT_SECRET-env"
    jwt_expire_minutes: int = 60 * 24

    # Sliding-window rate limit for the password endpoints (/auth/login, /auth/signup),
    # keyed by client IP. Format "N/second|minute|hour"; empty = disabled (dev/test default).
    auth_rate_limit: str = ""

    # ---- OIDC / Keycloak (used when auth_mode is hybrid|keycloak) ----
    # Issuer as it appears in tokens, e.g. https://auth.example.com/realms/tallymigration
    oidc_issuer: str = ""
    # Audience the API requires in access tokens (added by the realm's audience mapper).
    oidc_audience: str = "tallymigration-api"
    # JWKS endpoint; empty = derived from the issuer (Keycloak layout).
    oidc_jwks_url: str = ""
    # Public client id the SPA uses for the Authorization Code + PKCE flow.
    oidc_web_client_id: str = "tallymigration-web"
    # First OIDC login: link to an existing local account by *verified* email.
    oidc_link_by_email: bool = True
    # First OIDC login with no matching account: create user + personal org (like signup).
    oidc_jit_provisioning: bool = True

    @model_validator(mode="after")
    def enforce_prod_secret(self) -> Settings:
        if self.environment == "prod" and self.auth_mode != "keycloak" and self.jwt_secret == "dev-only-insecure-secret-change-me-via-TM_JWT_SECRET-env":
            raise ValueError("TM_JWT_SECRET must be explicitly set in 'prod' environment.")
        if self.auth_mode not in ("legacy", "hybrid", "keycloak"):
            raise ValueError("TM_AUTH_MODE must be one of: legacy, hybrid, keycloak.")
        if self.auth_mode in ("hybrid", "keycloak") and not self.oidc_issuer:
            raise ValueError("TM_OIDC_ISSUER is required when TM_AUTH_MODE is 'hybrid' or 'keycloak'.")
        return self

    @property
    def oidc_jwks_endpoint(self) -> str:
        if self.oidc_jwks_url:
            return self.oidc_jwks_url
        return self.oidc_issuer.rstrip("/") + "/protocol/openid-connect/certs"

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
    # Set explicit origins in prod (e.g. ["https://app.example.com"]). With the "*" wildcard the
    # app disables credentialed CORS (wildcard+credentials is an invalid, insecure combination).
    cors_origins: list[str] = ["*"]

    # ---- observability ----
    sentry_dsn: str = ""  # empty = Sentry disabled
    metrics_enabled: bool = True  # Prometheus /metrics endpoint

    # ---- dev-only DIRECT push to a local Tally gateway (bypasses the bridge) ----
    # In production the bridge relays XML; this is only for same-machine dev/E2E testing.
    direct_tally_push: bool = False
    tally_url: str = "http://127.0.0.1:9000"
    tally_timeout_seconds: float = 60.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
