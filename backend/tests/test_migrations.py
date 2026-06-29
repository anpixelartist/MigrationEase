"""Alembic migrations apply cleanly and produce the full schema."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import get_settings


def test_migrations_upgrade_head(tmp_path, monkeypatch):
    db_path = tmp_path / "migrated.db"
    url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("TM_DATABASE_URL", url)
    get_settings.cache_clear()

    cfg = Config("alembic.ini")  # tests run with cwd = backend/
    cfg.set_main_option("script_location", "alembic")
    command.upgrade(cfg, "head")  # runs initial schema + RLS (RLS no-ops on sqlite)

    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert {"organizations", "users", "memberships", "jobs", "bridges", "alembic_version"} <= tables
    get_settings.cache_clear()
