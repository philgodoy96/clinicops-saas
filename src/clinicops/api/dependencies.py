from collections.abc import Generator
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from clinicops.core.config import Settings
from clinicops.db.session import get_engine


def get_application_settings(request: Request) -> Settings:
    """Return the settings attached to the current application."""

    return cast(Settings, request.app.state.settings)


ApplicationSettingsDependency = Annotated[
    Settings,
    Depends(get_application_settings),
]


def get_database_session() -> Generator[Session, None, None]:
    """Yield one request-scoped SQLAlchemy session."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        if session.in_transaction():
            session.rollback()

        session.close()


DatabaseSessionDependency = Annotated[
    Session,
    Depends(get_database_session),
]
