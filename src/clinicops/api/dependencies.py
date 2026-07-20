from collections.abc import Generator
from typing import cast

from fastapi import Request
from sqlalchemy.orm import Session

from clinicops.core.config import Settings
from clinicops.db.session import get_engine


def get_application_settings(request: Request) -> Settings:
    """Return the settings attached to the current application."""

    return cast(Settings, request.app.state.settings)


def get_database_session() -> Generator[Session, None, None]:
    """Yield one request-scoped SQLAlchemy session."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        if session.in_transaction():
            session.rollback()

        session.close()
