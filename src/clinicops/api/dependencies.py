from collections.abc import Generator

from sqlalchemy.orm import Session

from clinicops.db.session import get_engine


def get_database_session() -> Generator[Session, None, None]:
    """Yield one request-scoped SQLAlchemy session."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        if session.in_transaction():
            session.rollback()

        session.close()
