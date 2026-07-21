from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from clinicops.cli.bootstrap_user import (
    BootstrapUserSettings,
    bootstrap_user,
)
from clinicops.db.session import get_engine
from clinicops.identity.email import canonicalize_email
from clinicops.identity.models import User, UserStatus
from clinicops.identity.passwords import Argon2PasswordHasher


def build_settings(
    *,
    email: str,
    password: str,
) -> BootstrapUserSettings:
    return BootstrapUserSettings.model_validate(
        {
            "email": email,
            "password": password,
        }
    )


def delete_user_by_email(email: str) -> None:
    canonical_email = canonicalize_email(email)

    with Session(get_engine()) as session:
        user = session.scalar(select(User).where(User.email == canonical_email))

        if user is None:
            return

        session.delete(user)
        session.commit()


def test_bootstrap_user_persists_active_identity_and_credential() -> None:
    email = f"bootstrap-{uuid4()}@northstar.example.com"
    password = "Bootstrap-ClinicOps-2026!"
    canonical_email = canonicalize_email(email)

    try:
        with Session(get_engine()) as session:
            result = bootstrap_user(
                session,
                build_settings(
                    email=email,
                    password=password,
                ),
            )

        assert result.created is True
        assert result.email == canonical_email

        with Session(get_engine()) as verification_session:
            persisted_user = verification_session.scalar(
                select(User).where(User.email == canonical_email)
            )

            assert persisted_user is not None
            assert persisted_user.id == result.user_id
            assert persisted_user.status is UserStatus.ACTIVE
            assert persisted_user.password_credential is not None

            password_hash = persisted_user.password_credential.password_hash

            assert password_hash != password
            assert Argon2PasswordHasher().verify(
                password,
                password_hash,
            )
    finally:
        delete_user_by_email(email)


def test_bootstrap_user_is_idempotent_without_password_reset() -> None:
    email = f"bootstrap-idempotent-{uuid4()}@northstar.example.com"
    original_password = "Original-ClinicOps-2026!"
    replacement_password = "Replacement-ClinicOps-2026!"
    canonical_email = canonicalize_email(email)

    try:
        with Session(get_engine()) as first_session:
            first_result = bootstrap_user(
                first_session,
                build_settings(
                    email=email,
                    password=original_password,
                ),
            )

        with Session(get_engine()) as second_session:
            second_result = bootstrap_user(
                second_session,
                build_settings(
                    email=email,
                    password=replacement_password,
                ),
            )

        assert first_result.created is True
        assert second_result.created is False
        assert second_result.user_id == first_result.user_id
        assert second_result.email == first_result.email

        with Session(get_engine()) as verification_session:
            user_count = verification_session.scalar(
                select(func.count()).select_from(User).where(User.email == canonical_email)
            )
            persisted_user = verification_session.scalar(
                select(User).where(User.email == canonical_email)
            )

            assert user_count == 1
            assert persisted_user is not None
            assert persisted_user.password_credential is not None

            password_hash = persisted_user.password_credential.password_hash
            password_hasher = Argon2PasswordHasher()

            assert password_hasher.verify(
                original_password,
                password_hash,
            )
            assert not password_hasher.verify(
                replacement_password,
                password_hash,
            )
    finally:
        delete_user_by_email(email)
