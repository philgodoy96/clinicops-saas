from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from clinicops.core.config import get_settings
from clinicops.db.exceptions import DatabaseUnavailableError


@lru_cache
def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine."""

    settings = get_settings()

    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": settings.database_connect_timeout_seconds,
        },
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide SQLAlchemy session factory."""

    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        expire_on_commit=False,
        autocommit=False,
    )


def get_db_session() -> Iterator[Session]:
    """Provide a database session and always close it after use."""

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def check_database_connection() -> None:
    """Execute a minimal query to confirm database availability."""

    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError from exc
