"""SQLAlchemy engine, session factory and declarative base.

PostgreSQL only. Per D004 and the S03 forbidden scope, SQLite is never used as
a stand-in - the container in `09_Code/docker-compose.postgres.yml` is the one
supported database for development and for tests.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for every FindIt table."""


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Build an engine for `url`, defaulting to the configured database.

    `pool_pre_ping` keeps long-lived sessions usable after the container is
    restarted, which happens routinely during hardware bring-up.
    """
    target = url or get_settings().database_url
    if target.startswith("sqlite"):
        raise ValueError(
            "SQLite is not permitted for this project - PostgreSQL runs in Docker (D004)."
        )
    return create_engine(target, echo=echo, pool_pre_ping=True, future=True)


engine: Engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session] = SessionLocal) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any exception."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
