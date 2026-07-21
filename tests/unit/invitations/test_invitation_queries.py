from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from clinicops.invitations.models import (
    Invitation,
    InvitationStatus,
)
from clinicops.invitations.query_repository import (
    InvitationQueryRepository,
)
from clinicops.invitations.services.queries import (
    ListTenantInvitationsCommand,
    ListTenantInvitationsService,
    TenantInvitation,
)
from clinicops.tenancy.models import TenantRole

FIXED_NOW = datetime(2026, 8, 13, 11, 0, tzinfo=UTC)


class RecordingSession:
    """Track accidental writes in invitation query services."""

    def __init__(self) -> None:
        self.commit_count = 0
        self.flush_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def flush(self) -> None:
        self.flush_count += 1


class FakeInvitationQueryRepository:
    """Return deterministic invitation records."""

    def __init__(
        self,
        invitations: list[Invitation],
    ) -> None:
        self.invitations = invitations
        self.received_tenant_id: UUID | None = None

    def list_for_tenant(
        self,
        session: Session,
        tenant_id: UUID,
    ) -> list[Invitation]:
        self.received_tenant_id = tenant_id
        return self.invitations


def build_invitation(
    tenant_id: UUID,
    *,
    status: InvitationStatus,
    invited_email: str,
) -> Invitation:
    accepted_at = FIXED_NOW if status is InvitationStatus.ACCEPTED else None
    revoked_at = FIXED_NOW if status is InvitationStatus.REVOKED else None

    return Invitation(
        id=uuid4(),
        tenant_id=tenant_id,
        invited_email=invited_email,
        role=TenantRole.STAFF,
        status=status,
        token_digest="a" * 64,
        created_by_membership_id=uuid4(),
        accepted_by_user_id=(uuid4() if status is InvitationStatus.ACCEPTED else None),
        expires_at=FIXED_NOW + timedelta(days=7),
        accepted_at=accepted_at,
        revoked_at=revoked_at,
        created_at=FIXED_NOW - timedelta(days=1),
        updated_at=FIXED_NOW,
    )


def test_list_tenant_invitations_maps_public_read_models_without_writes() -> None:
    tenant_id = uuid4()
    pending = build_invitation(
        tenant_id,
        status=InvitationStatus.PENDING,
        invited_email="pending@example.com",
    )
    revoked = build_invitation(
        tenant_id,
        status=InvitationStatus.REVOKED,
        invited_email="revoked@example.com",
    )
    repository = FakeInvitationQueryRepository([pending, revoked])
    session = RecordingSession()
    service = ListTenantInvitationsService(cast(InvitationQueryRepository, repository))

    results = service.execute(
        cast(Session, session),
        ListTenantInvitationsCommand(
            tenant_id=tenant_id,
        ),
    )

    assert repository.received_tenant_id == tenant_id
    assert [result.id for result in results] == [
        pending.id,
        revoked.id,
    ]
    assert results[0].status is InvitationStatus.PENDING
    assert results[1].status is InvitationStatus.REVOKED
    assert results[1].revoked_at == FIXED_NOW
    assert session.commit_count == 0
    assert session.flush_count == 0


def test_invitation_read_model_excludes_secrets_and_internal_actor_ids() -> None:
    public_fields = {field.name for field in fields(TenantInvitation)}

    assert "token" not in public_fields
    assert "token_digest" not in public_fields
    assert "created_by_membership_id" not in public_fields
    assert "accepted_by_user_id" not in public_fields
