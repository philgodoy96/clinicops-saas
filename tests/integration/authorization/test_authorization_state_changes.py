from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from clinicops.authentication.access_tokens import AccessTokenCodec
from clinicops.authentication.config import AuthenticationTokenConfig
from clinicops.authentication.exceptions import (
    AuthenticationSessionInactiveError,
)
from clinicops.authentication.models import (
    AuthSession,
    AuthSessionStatus,
)
from clinicops.authentication.services.resolve_principal import (
    AuthenticatedPrincipal,
    ResolveAuthenticatedPrincipalCommand,
    ResolveAuthenticatedPrincipalService,
)
from clinicops.authorization.exceptions import (
    TenantDisabledError,
    TenantMembershipDisabledError,
    TenantPermissionDeniedError,
)
from clinicops.authorization.permissions import TenantPermission
from clinicops.authorization.services.require_permission import (
    RequireTenantPermissionCommand,
    RequireTenantPermissionService,
)
from clinicops.authorization.services.resolve_tenant_context import (
    ResolveTenantContextCommand,
    ResolveTenantContextService,
    TenantContext,
)
from clinicops.db.session import get_engine
from clinicops.identity.exceptions import UserDisabledError
from clinicops.identity.models import User, UserStatus
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)

FIXED_NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)
TOKEN_ISSUED_AT = FIXED_NOW - timedelta(minutes=5)
SESSION_CREATED_AT = FIXED_NOW - timedelta(days=1)
SESSION_EXPIRES_AT = FIXED_NOW + timedelta(days=29)
SIGNING_KEY = "development-signing-key-with-32-bytes-minimum"


class FixedClock:
    """Return a deterministic timezone-aware datetime."""

    def now(self) -> datetime:
        return FIXED_NOW


@dataclass(frozen=True, slots=True)
class AuthorizationFixture:
    """Committed authorization state used across request sessions."""

    user_id: UUID
    session_id: UUID
    tenant_id: UUID
    membership_id: UUID
    access_token: str


def build_access_token_codec() -> AccessTokenCodec:
    """Build deterministic access-token configuration."""

    return AccessTokenCodec(
        AuthenticationTokenConfig(
            issuer="clinicops",
            audience="clinicops-api",
            signing_key=SIGNING_KEY,
        )
    )


def build_principal_service() -> ResolveAuthenticatedPrincipalService:
    """Build principal resolution with deterministic time."""

    return ResolveAuthenticatedPrincipalService(
        access_token_codec=build_access_token_codec(),
        clock=FixedClock(),
    )


def persist_authorization_fixture(
    *,
    role: TenantRole = TenantRole.ADMIN,
) -> AuthorizationFixture:
    """Persist one active authentication and tenant context."""

    with Session(get_engine()) as session:
        user = User(email=f"user-{uuid4()}@example.com")
        tenant = Tenant(
            name=f"Clinic {uuid4().hex[:8]}",
        )

        session.add_all([user, tenant])
        session.flush()

        auth_session = AuthSession(
            user_id=user.id,
            expires_at=SESSION_EXPIRES_AT,
            last_rotated_at=SESSION_CREATED_AT,
            created_at=SESSION_CREATED_AT,
        )
        membership = Membership(
            user_id=user.id,
            tenant_id=tenant.id,
            role=role,
        )

        session.add_all([auth_session, membership])
        session.flush()

        issued_token = build_access_token_codec().issue(
            user_id=user.id,
            session_id=auth_session.id,
            issued_at=TOKEN_ISSUED_AT,
        )
        fixture = AuthorizationFixture(
            user_id=user.id,
            session_id=auth_session.id,
            tenant_id=tenant.id,
            membership_id=membership.id,
            access_token=issued_token.token,
        )
        session.commit()

    return fixture


def delete_authorization_fixture(
    fixture: AuthorizationFixture,
) -> None:
    """Delete one committed authorization fixture."""

    with Session(get_engine()) as session:
        session.execute(delete(AuthSession).where(AuthSession.id == fixture.session_id))
        session.execute(delete(Membership).where(Membership.id == fixture.membership_id))
        session.execute(delete(Tenant).where(Tenant.id == fixture.tenant_id))
        session.execute(delete(User).where(User.id == fixture.user_id))
        session.commit()


def resolve_principal(
    session: Session,
    fixture: AuthorizationFixture,
) -> AuthenticatedPrincipal:
    """Resolve one principal as a protected request would."""

    return build_principal_service().execute(
        session,
        ResolveAuthenticatedPrincipalCommand(access_token=fixture.access_token),
    )


def resolve_tenant_context(
    session: Session,
    fixture: AuthorizationFixture,
) -> TenantContext:
    """Resolve principal and tenant context from current state."""

    principal = resolve_principal(session, fixture)

    return ResolveTenantContextService().execute(
        session,
        ResolveTenantContextCommand(
            principal=principal,
            tenant_id=fixture.tenant_id,
        ),
    )


def test_revoked_session_blocks_next_principal_resolution() -> None:
    fixture = persist_authorization_fixture()

    try:
        with Session(get_engine()) as initial_request:
            principal = resolve_principal(
                initial_request,
                fixture,
            )
            assert principal.session_id == fixture.session_id

        with Session(get_engine()) as transition_session:
            auth_session = transition_session.get(
                AuthSession,
                fixture.session_id,
            )
            assert auth_session is not None

            auth_session.status = AuthSessionStatus.REVOKED
            auth_session.revoked_at = FIXED_NOW
            transition_session.commit()

        with (
            Session(get_engine()) as next_request,
            pytest.raises(AuthenticationSessionInactiveError),
        ):
            resolve_principal(next_request, fixture)
    finally:
        delete_authorization_fixture(fixture)


def test_disabled_user_blocks_next_principal_resolution() -> None:
    fixture = persist_authorization_fixture()

    try:
        with Session(get_engine()) as initial_request:
            principal = resolve_principal(
                initial_request,
                fixture,
            )
            assert principal.user_id == fixture.user_id

        with Session(get_engine()) as transition_session:
            user = transition_session.get(User, fixture.user_id)
            assert user is not None

            user.status = UserStatus.DISABLED
            user.disabled_at = FIXED_NOW
            transition_session.commit()

        with Session(get_engine()) as next_request, pytest.raises(UserDisabledError):
            resolve_principal(next_request, fixture)
    finally:
        delete_authorization_fixture(fixture)


def test_disabled_tenant_blocks_next_context_resolution() -> None:
    fixture = persist_authorization_fixture()

    try:
        with Session(get_engine()) as initial_request:
            context = resolve_tenant_context(
                initial_request,
                fixture,
            )
            assert context.tenant_id == fixture.tenant_id

        with Session(get_engine()) as transition_session:
            tenant = transition_session.get(
                Tenant,
                fixture.tenant_id,
            )
            assert tenant is not None

            tenant.status = TenantStatus.DISABLED
            tenant.disabled_at = FIXED_NOW
            transition_session.commit()

        with Session(get_engine()) as next_request, pytest.raises(TenantDisabledError):
            resolve_tenant_context(next_request, fixture)
    finally:
        delete_authorization_fixture(fixture)


def test_disabled_membership_blocks_next_context_resolution() -> None:
    fixture = persist_authorization_fixture()

    try:
        with Session(get_engine()) as initial_request:
            context = resolve_tenant_context(
                initial_request,
                fixture,
            )
            assert context.membership_id == fixture.membership_id

        with Session(get_engine()) as transition_session:
            membership = transition_session.get(
                Membership,
                fixture.membership_id,
            )
            assert membership is not None

            membership.status = MembershipStatus.DISABLED
            membership.disabled_at = FIXED_NOW
            transition_session.commit()

        with (
            Session(get_engine()) as next_request,
            pytest.raises(TenantMembershipDisabledError),
        ):
            resolve_tenant_context(next_request, fixture)
    finally:
        delete_authorization_fixture(fixture)


def test_role_demotion_removes_permission_on_next_resolution() -> None:
    fixture = persist_authorization_fixture(
        role=TenantRole.ADMIN,
    )

    try:
        with Session(get_engine()) as initial_request:
            initial_context = resolve_tenant_context(
                initial_request,
                fixture,
            )
            initial_authorized_context = RequireTenantPermissionService().execute(
                RequireTenantPermissionCommand(
                    tenant_context=initial_context,
                    permission=(TenantPermission.MEMBER_INVITE),
                )
            )

            assert initial_context.role is TenantRole.ADMIN
            assert initial_authorized_context.granted_permission is TenantPermission.MEMBER_INVITE

        with Session(get_engine()) as transition_session:
            membership = transition_session.get(
                Membership,
                fixture.membership_id,
            )
            assert membership is not None

            membership.role = TenantRole.STAFF
            transition_session.commit()

        with Session(get_engine()) as next_request:
            current_context = resolve_tenant_context(
                next_request,
                fixture,
            )

            assert current_context.role is TenantRole.STAFF

            with pytest.raises(TenantPermissionDeniedError):
                RequireTenantPermissionService().execute(
                    RequireTenantPermissionCommand(
                        tenant_context=current_context,
                        permission=(TenantPermission.MEMBER_INVITE),
                    )
                )
    finally:
        delete_authorization_fixture(fixture)
