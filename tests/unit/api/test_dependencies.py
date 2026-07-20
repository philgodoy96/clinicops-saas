from typing import cast

import pytest

from clinicops.api import dependencies as api_dependencies


class FakeSession:
    """Track request-session cleanup behavior."""

    def __init__(self, *, has_transaction: bool) -> None:
        self._has_transaction = has_transaction
        self.rollback_called = False
        self.close_called = False

    def in_transaction(self) -> bool:
        return self._has_transaction

    def rollback(self) -> None:
        self.rollback_called = True

    def close(self) -> None:
        self.close_called = True


def install_fake_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    has_transaction: bool,
) -> FakeSession:
    """Replace SQLAlchemy construction with a tracked fake."""

    expected_engine = object()
    fake_session = FakeSession(
        has_transaction=has_transaction,
    )

    def session_factory(bind: object) -> FakeSession:
        assert bind is expected_engine
        return fake_session

    monkeypatch.setattr(
        api_dependencies,
        "get_engine",
        lambda: expected_engine,
    )
    monkeypatch.setattr(
        api_dependencies,
        "Session",
        session_factory,
    )

    return fake_session


def test_database_dependency_rolls_back_open_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = install_fake_session(
        monkeypatch,
        has_transaction=True,
    )
    dependency = api_dependencies.get_database_session()

    yielded_session = next(dependency)
    dependency.close()

    assert cast(FakeSession, yielded_session) is fake_session
    assert fake_session.rollback_called is True
    assert fake_session.close_called is True


def test_database_dependency_closes_session_without_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = install_fake_session(
        monkeypatch,
        has_transaction=False,
    )
    dependency = api_dependencies.get_database_session()

    yielded_session = next(dependency)
    dependency.close()

    assert cast(FakeSession, yielded_session) is fake_session
    assert fake_session.rollback_called is False
    assert fake_session.close_called is True
