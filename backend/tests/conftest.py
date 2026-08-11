"""Test bootstrap for database-backed tests.

Tests run against a real PostgreSQL database (`findit_test`) on the same server
started by `09_Code/docker-compose.postgres.yml`. SQLite is deliberately not
used as a stand-in - see D004 and the S03 forbidden scope. If the container is
not running these tests fail loudly rather than silently skipping.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database import Base

TEST_DB_NAME = "findit_test"


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    """A clean `findit_test` database, created on the dev PostgreSQL server."""
    url = make_url(get_settings().database_url)
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB_NAME}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    finally:
        admin_engine.dispose()

    engine = create_engine(url.set(database=TEST_DB_NAME), future=True, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def db_session(test_engine: Engine) -> Iterator[Session]:
    """A session wrapped in a transaction that is rolled back after each test,
    so tests never see one another's rows."""
    connection = test_engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        # Tests that assert on IntegrityError leave the transaction already
        # unwound, so only roll back when there is still something to undo.
        if transaction.is_active:
            transaction.rollback()
        connection.close()
