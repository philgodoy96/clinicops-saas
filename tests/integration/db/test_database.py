from sqlalchemy import text

from clinicops.db.session import get_engine


def test_postgresql_accepts_queries() -> None:
    with get_engine().connect() as connection:
        result = connection.execute(text("SELECT 1")).scalar_one()

    assert result == 1
