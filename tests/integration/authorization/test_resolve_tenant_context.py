from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from clinicops.authentication.services.resolve_principal import (
    AuthenticatedPrincipal,
)
from clinicops.authorization.exceptions import (
    TenantDisabledError,
    TenantMembershipDisabledError,
    TenantMembershipNotFoundError,
    TenantNotFoundError,
)
from clinicops.authorization.services.resolve_tenant_context import (
    ResolveTenantContextCommand,
    ResolveTenantContextService,
)
from clinicops.db.session import get_engine
from clinicops.identity.models import User
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)

FIXED_NOW = datetime(2026, 7, 29, 15, 0, tzinfo=UTC)


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Provide an isolated database session rolled back after each test."""

    session = Session(get_engine())

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def create_user(session: Session) -> User:
    """Persist one active global user."""

    user = User(email=f"user-{uuid4()}@example.com")
    session.add(user)
    session.flush()

    return user


def create_tenant(
    session: Session,
    *,
    status: TenantStatus = TenantStatus.ACTIVE,
) -> Tenant:
    """Persist one tenant."""

    suffix = uuid4().hex
    tenant = Tenant(
        name=f"Clinic {suffix[:8]}",
        status=status,
        disabled_at=(FIXED_NOW if status is TenantStatus.DISABLED else None),
    )
    session.add(tenant)
    session.flush()

    return tenant


def create_membership(
    session: Session,
    *,
    user: User,
    tenant: Tenant,
    role: TenantRole = TenantRole.STAFF,
    status: MembershipStatus = MembershipStatus.ACTIVE,
) -> Membership:
    """Persist one tenant membership."""

    membership = Membership(
        user_id=user.id,
        tenant_id=tenant.id,
        role=role,
        status=status,
        disabled_at=(FIXED_NOW if status is MembershipStatus.DISABLED else None),
    )
    session.add(membership)
    session.flush()

    return membership


def build_principal(user: User) -> AuthenticatedPrincipal:
    """Build a trusted global principal for tenant-resolution tests."""

    return AuthenticatedPrincipal(
        user_id=user.id,
        session_id=uuid4(),
        access_token_id=uuid4(),
        authenticated_at=FIXED_NOW - timedelta(minutes=5),
        access_token_expires_at=FIXED_NOW + timedelta(minutes=10),
        session_expires_at=FIXED_NOW + timedelta(days=29),
    )


@pytest.mark.parametrize(
    "role",
    [
        TenantRole.OWNER,
        TenantRole.ADMIN,
        TenantRole.STAFF,
    ],
)
def test_active_membership_resolves_tenant_context(
    db_session: Session,
    role: TenantRole,
) -> None:
    user = create_user(db_session)
    tenant = create_tenant(db_session)
    membership = create_membership(
        db_session,
        user=user,
        tenant=tenant,
        role=role,
    )
    principal = build_principal(user)

    context = ResolveTenantContextService().execute(
        db_session,
        ResolveTenantContextCommand(
            principal=principal,
            tenant_id=tenant.id,
        ),
    )

    assert context.user_id == principal.user_id
    assert context.session_id == principal.session_id
    assert context.tenant_id == tenant.id
    assert context.membership_id == membership.id
    assert context.role is role


def test_missing_tenant_cannot_resolve_context(
    db_session: Session,
) -> None:
    user = create_user(db_session)

    with pytest.raises(TenantNotFoundError):
        ResolveTenantContextService().execute(
            db_session,
            ResolveTenantContextCommand(
                principal=build_principal(user),
                tenant_id=uuid4(),
            ),
        )


def test_user_without_membership_cannot_resolve_context(
    db_session: Session,
) -> None:
    user = create_user(db_session)
    tenant = create_tenant(db_session)

    with pytest.raises(TenantMembershipNotFoundError):
        ResolveTenantContextService().execute(
            db_session,
            ResolveTenantContextCommand(
                principal=build_principal(user),
                tenant_id=tenant.id,
            ),
        )


def test_membership_in_another_tenant_does_not_grant_access(
    db_session: Session,
) -> None:
    user = create_user(db_session)
    selected_tenant = create_tenant(db_session)
    other_tenant = create_tenant(db_session)
    create_membership(
        db_session,
        user=user,
        tenant=other_tenant,
        role=TenantRole.OWNER,
    )

    with pytest.raises(TenantMembershipNotFoundError):
        ResolveTenantContextService().execute(
            db_session,
            ResolveTenantContextCommand(
                principal=build_principal(user),
                tenant_id=selected_tenant.id,
            ),
        )


def test_another_users_membership_does_not_grant_access(
    db_session: Session,
) -> None:
    principal_user = create_user(db_session)
    membership_owner = create_user(db_session)
    tenant = create_tenant(db_session)
    create_membership(
        db_session,
        user=membership_owner,
        tenant=tenant,
        role=TenantRole.OWNER,
    )

    with pytest.raises(TenantMembershipNotFoundError):
        ResolveTenantContextService().execute(
            db_session,
            ResolveTenantContextCommand(
                principal=build_principal(principal_user),
                tenant_id=tenant.id,
            ),
        )


def test_disabled_tenant_cannot_resolve_context(
    db_session: Session,
) -> None:
    user = create_user(db_session)
    tenant = create_tenant(
        db_session,
        status=TenantStatus.DISABLED,
    )
    create_membership(
        db_session,
        user=user,
        tenant=tenant,
    )

    with pytest.raises(TenantDisabledError):
        ResolveTenantContextService().execute(
            db_session,
            ResolveTenantContextCommand(
                principal=build_principal(user),
                tenant_id=tenant.id,
            ),
        )


def test_disabled_membership_cannot_resolve_context(
    db_session: Session,
) -> None:
    user = create_user(db_session)
    tenant = create_tenant(db_session)
    create_membership(
        db_session,
        user=user,
        tenant=tenant,
        status=MembershipStatus.DISABLED,
    )

    with pytest.raises(TenantMembershipDisabledError):
        ResolveTenantContextService().execute(
            db_session,
            ResolveTenantContextCommand(
                principal=build_principal(user),
                tenant_id=tenant.id,
            ),
        )
