"""SQLAlchemy engine/session wiring (sync; SQLite for dev, Postgres-ready).

Sync is intentional: the whole job pipeline runs synchronously inside the request threadpool, so a
sync session composes cleanly with it. Swapping to Postgres is just a ``TM_DATABASE_URL`` change
(e.g. ``postgresql+psycopg://...``); RLS/tenant policies are a prod migration concern (tenant
isolation is also enforced at the repository layer by ``org_id``).
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _init() -> None:
    global _engine, _SessionLocal
    url = get_settings().database_url
    kwargs: dict = {}
    if url.startswith("sqlite"):
        # sessions are used across threadpool threads -> disable the thread check;
        # in-memory DBs need a single shared connection (StaticPool).
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    _engine = create_engine(url, future=True, **kwargs)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def engine() -> Engine:
    if _engine is None:
        _init()
    assert _engine is not None
    return _engine


def new_session() -> Session:
    if _SessionLocal is None:
        _init()
    assert _SessionLocal is not None
    return _SessionLocal()


def init_db() -> None:
    from app.db import models  # noqa: F401  (register mappers before create_all)

    Base.metadata.create_all(engine())


def set_tenant(session: Session, org_id: str) -> None:
    """Bind the tenant for Postgres RLS (no-op on other dialects).

    Must be called at the start of each tenant transaction when RLS is enabled (see the
    0002_rls_policies migration). Uses a parameterized GUC to avoid injection.
    """
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT set_config('app.current_org', :org, true)"), {"org": org_id})


def reset_engine() -> None:
    """Test helper: drop the cached engine so a new TM_DATABASE_URL takes effect."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
