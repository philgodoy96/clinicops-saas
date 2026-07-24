from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from clinicops.api.dependencies import get_database_session
from clinicops.api.errors import PROBLEM_MEDIA_TYPE
from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import RecordAuditLogCommand, RecordedAuditLog
from clinicops.audit.models import AuditLogEntry
from clinicops.audit.recording import AuditRecorder
from clinicops.authentication.models import AuthSession
from clinicops.core.config import Settings
from clinicops.db.session import get_engine
from clinicops.identity.models import (
    PasswordCredential,
    User,
    UserStatus,
)
from clinicops.identity.passwords import Argon2PasswordHasher
from clinicops.main import create_app
from clinicops.professionals.contracts import (
    ArchiveProfessionalCommand,
    CreateProfessionalCommand,
    LinkProfessionalMembershipCommand,
    RestoreProfessionalCommand,
    UnlinkProfessionalMembershipCommand,
    UpdateProfessionalCommand,
)
from clinicops.professionals.enums import (
    ProfessionalMutableField,
    ProfessionalStatus,
)
from clinicops.professionals.models import Professional
from clinicops.professionals.repositories.professional_repository import (
    ProfessionalRepository,
)
from clinicops.professionals.services.archive_professional import (
    ArchiveProfessionalService,
)
from clinicops.professionals.services.create_professional import (
    CreateProfessionalService,
)
from clinicops.professionals.services.link_professional_membership import (
    LinkProfessionalMembershipService,
)
from clinicops.professionals.services.restore_professional import (
    RestoreProfessionalService,
)
from clinicops.professionals.services.unlink_professional_for_membership_removal import (
    UnlinkProfessionalForMembershipRemovalService,
)
from clinicops.professionals.services.unlink_professional_membership import (
    UnlinkProfessionalMembershipService,
)
from clinicops.professionals.services.update_professional import (
    UpdateProfessionalService,
)
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)
from clinicops.tenancy.services.membership_administration import (
    RemoveMembershipCommand,
)
from clinicops.tenancy.services.remove_membership import (
    RemoveMembershipService,
)

PASSWORD = "Correct-Horse-Battery-Staple-2026!"
REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"

PROFESSIONAL_COLLECTION_PATH = "/api/v1/tenants/{tenant_id}/professionals"
PROFESSIONAL_ITEM_PATH = "/api/v1/tenants/{tenant_id}/professionals/{professional_id}"
PROFESSIONAL_LINK_PATH = (
    "/api/v1/tenants/{tenant_id}/professionals/{professional_id}/link-membership"
)

_FORBIDDEN_PROFILE_VALUES = {
    "Morgan Reed",
    "Alex Morgan",
    "Jordan Lee",
    "Dentistry",
    "Orthodontics",
    "Cardiology",
    "DDS-48291",
    "CRM-12345",
    "CA",
    "SP",
    "morgan@example.com",
    "alex@example.com",
    "jordan@example.com",
    "updated@example.com",
    "loser@example.com",
    "winner@example.com",
    "+1-202-555-0130",
    "+1-202-555-0199",
    "PROVIDER-100",
    "PROVIDER-200",
    "PROVIDER-DUP",
    "PROVIDER-CONFLICT",
}


class SimulatedAuditRecordingError(RuntimeError):
    """Represent a deterministic audit recording failure."""


class FailingAuditRecorder:
    """Raise without committing or rolling back the caller Session."""

    def record(
        self,
        session: Session,
        command: RecordAuditLogCommand,
    ) -> RecordedAuditLog:
        raise SimulatedAuditRecordingError("audit recording failed")


@dataclass(frozen=True, slots=True)
class CommittedUser:
    id: UUID
    email: str


@dataclass(frozen=True, slots=True)
class CommittedTenant:
    id: UUID


@dataclass(frozen=True, slots=True)
class CommittedMembership:
    id: UUID
    user_id: UUID
    role: TenantRole
    status: MembershipStatus


@dataclass(frozen=True, slots=True)
class CommittedProfessional:
    id: UUID
    version: int
    membership_id: UUID | None
    full_name: str
    specialty: str | None
    registration_number: str | None
    registration_region: str | None
    email: str | None
    phone: str | None
    external_reference: str | None
    status: ProfessionalStatus


class ProfessionalIntegrityDataFactory:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._user_ids: set[UUID] = set()
        self._tenant_ids: set[UUID] = set()
        self._password_hash = Argon2PasswordHasher().hash(PASSWORD)

    def create_user(
        self,
        *,
        email_prefix: str,
    ) -> CommittedUser:
        user = User(
            email=f"{email_prefix}-{uuid4().hex}@example.com",
            status=UserStatus.ACTIVE,
            password_credential=PasswordCredential(
                password_hash=self._password_hash,
            ),
        )
        self._session.add(user)
        self._session.commit()
        self._session.refresh(user)
        self._user_ids.add(user.id)
        return CommittedUser(id=user.id, email=user.email)

    def create_tenant(
        self,
        *,
        owner: CommittedUser,
        name: str,
    ) -> tuple[CommittedTenant, CommittedMembership]:
        tenant = Tenant(
            name=f"{name} {uuid4().hex[:8]}",
            status=TenantStatus.ACTIVE,
        )
        owner_membership = Membership(
            tenant=tenant,
            user_id=owner.id,
            role=TenantRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        self._session.add_all([tenant, owner_membership])
        self._session.commit()
        self._session.refresh(tenant)
        self._session.refresh(owner_membership)
        self._tenant_ids.add(tenant.id)
        return (
            CommittedTenant(id=tenant.id),
            CommittedMembership(
                id=owner_membership.id,
                user_id=owner.id,
                role=owner_membership.role,
                status=owner_membership.status,
            ),
        )

    def add_membership(
        self,
        *,
        tenant: CommittedTenant,
        user: CommittedUser,
        role: TenantRole = TenantRole.STAFF,
    ) -> CommittedMembership:
        membership = Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            status=MembershipStatus.ACTIVE,
        )
        self._session.add(membership)
        self._session.commit()
        self._session.refresh(membership)
        return CommittedMembership(
            id=membership.id,
            user_id=user.id,
            role=membership.role,
            status=membership.status,
        )

    def create_professional(
        self,
        *,
        tenant: CommittedTenant,
        full_name: str = "Morgan Reed",
        specialty: str | None = "Dentistry",
        registration_number: str | None = "DDS-48291",
        registration_region: str | None = "CA",
        email: str | None = "morgan@example.com",
        phone: str | None = "+1-202-555-0130",
        external_reference: str | None = "PROVIDER-100",
        membership_id: UUID | None = None,
        status: ProfessionalStatus = ProfessionalStatus.ACTIVE,
        version: int = 1,
    ) -> CommittedProfessional:
        professional = Professional(
            tenant_id=tenant.id,
            membership_id=membership_id,
            full_name=full_name,
            specialty=specialty,
            registration_number=registration_number,
            registration_region=registration_region,
            email=email,
            phone=phone,
            external_reference=external_reference,
            status=status,
            version=version,
        )
        self._session.add(professional)
        self._session.commit()
        self._session.refresh(professional)
        return CommittedProfessional(
            id=professional.id,
            version=professional.version,
            membership_id=professional.membership_id,
            full_name=professional.full_name,
            specialty=professional.specialty,
            registration_number=professional.registration_number,
            registration_region=professional.registration_region,
            email=professional.email,
            phone=professional.phone,
            external_reference=professional.external_reference,
            status=professional.status,
        )

    def cleanup(self) -> None:
        self._session.rollback()
        self._session.expunge_all()

        if self._user_ids:
            self._session.execute(
                delete(AuthSession)
                .where(AuthSession.user_id.in_(self._user_ids))
                .execution_options(synchronize_session=False)
            )

        if self._tenant_ids:
            self._session.execute(
                delete(AuditLogEntry)
                .where(AuditLogEntry.tenant_id.in_(self._tenant_ids))
                .execution_options(synchronize_session=False)
            )
            self._session.execute(
                delete(Professional)
                .where(Professional.tenant_id.in_(self._tenant_ids))
                .execution_options(synchronize_session=False)
            )
            self._session.execute(
                delete(Membership)
                .where(Membership.tenant_id.in_(self._tenant_ids))
                .execution_options(synchronize_session=False)
            )
            self._session.execute(
                delete(Tenant)
                .where(Tenant.id.in_(self._tenant_ids))
                .execution_options(synchronize_session=False)
            )

        if self._user_ids:
            self._session.execute(
                delete(User)
                .where(User.id.in_(self._user_ids))
                .execution_options(synchronize_session=False)
            )

        self._session.commit()


@pytest.fixture
def integrity_data_factory() -> Iterator[ProfessionalIntegrityDataFactory]:
    session = Session(get_engine())
    factory = ProfessionalIntegrityDataFactory(session)

    try:
        yield factory
    finally:
        factory.cleanup()
        session.close()


def _audit_context(*, user_id: UUID) -> AuditRecordingContext:
    return AuditRecordingContext.http_user(
        user_id=user_id,
        role=TenantRole.ADMIN.value,
        request_id="request-professional-integrity",
        correlation_id="correlation-professional-integrity",
    )


def _failing_recorder() -> AuditRecorder:
    return cast(AuditRecorder, FailingAuditRecorder())


def _professional_audit_entries(
    session: Session,
    *,
    tenant_id: UUID,
    resource_id: str | None = None,
) -> tuple[AuditLogEntry, ...]:
    statement = select(AuditLogEntry).where(
        AuditLogEntry.tenant_id == tenant_id,
        AuditLogEntry.resource_type == AuditResourceType.PROFESSIONAL.value,
    )
    if resource_id is not None:
        statement = statement.where(AuditLogEntry.resource_id == resource_id)
    return tuple(session.execute(statement).scalars())


def _membership_audit_entries(
    session: Session,
    *,
    tenant_id: UUID,
    resource_id: str,
) -> tuple[AuditLogEntry, ...]:
    return tuple(
        session.execute(
            select(AuditLogEntry).where(
                AuditLogEntry.tenant_id == tenant_id,
                AuditLogEntry.resource_type == AuditResourceType.MEMBERSHIP.value,
                AuditLogEntry.resource_id == resource_id,
            )
        ).scalars()
    )


def _metadata_strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        strings: set[str] = set()
        for item in value:
            strings.update(_metadata_strings(item))
        return strings
    if isinstance(value, dict):
        strings = set()
        for key, item in value.items():
            strings.add(str(key))
            strings.update(_metadata_strings(item))
        return strings
    return set()


def _assert_audit_metadata_safe(entries: tuple[AuditLogEntry, ...]) -> None:
    for entry in entries:
        assert _FORBIDDEN_PROFILE_VALUES.isdisjoint(_metadata_strings(entry.event_metadata))


def _fresh_professional(
    *,
    tenant_id: UUID,
    professional_id: UUID,
) -> Professional | None:
    session = Session(get_engine())
    try:
        return session.execute(
            select(Professional).where(
                Professional.id == professional_id,
                Professional.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()
    finally:
        session.close()


def _fresh_membership(*, membership_id: UUID) -> Membership | None:
    session = Session(get_engine())
    try:
        return session.get(Membership, membership_id)
    finally:
        session.close()


def login_headers(
    client: TestClient,
    user: CommittedUser,
) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": user.email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def professionals_collection_path(tenant_id: UUID) -> str:
    return PROFESSIONAL_COLLECTION_PATH.format(tenant_id=tenant_id)


def professional_path(tenant_id: UUID, professional_id: UUID | str) -> str:
    return PROFESSIONAL_ITEM_PATH.format(
        tenant_id=tenant_id,
        professional_id=professional_id,
    )


def create_professional_via_api(
    client: TestClient,
    *,
    tenant_id: UUID,
    headers: dict[str, str],
    payload: dict[str, object] | None = None,
) -> Response:
    body: dict[str, object] = {
        "full_name": "Morgan Reed",
        "specialty": "Dentistry",
        "registration_number": "DDS-48291",
        "registration_region": "CA",
        "email": "morgan@example.com",
        "phone": "+1-202-555-0130",
        "external_reference": "PROVIDER-100",
    }
    if payload is not None:
        body.update(payload)
    return client.post(
        professionals_collection_path(tenant_id),
        headers=headers,
        json=body,
    )


def assert_problem(
    response: Response,
    *,
    status_code: int,
    code: str,
) -> dict[str, object]:
    body = cast(dict[str, object], response.json())
    assert response.status_code == status_code
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body["status"] == status_code
    assert body["code"] == code
    assert body["type"] == f"urn:clinicops:problem:{code}"
    return body


def _tracked_session_override(
    commit_log: list[str],
) -> Iterator[Session]:
    session = Session(get_engine())
    original_commit = session.commit
    original_rollback = session.rollback

    def tracked_commit() -> None:
        commit_log.append("commit")
        original_commit()

    def tracked_rollback() -> None:
        commit_log.append("rollback")
        original_rollback()

    session.commit = tracked_commit  # type: ignore[method-assign]
    session.rollback = tracked_rollback  # type: ignore[method-assign]

    try:
        yield session
    finally:
        if session.in_transaction():
            original_rollback()
        session.close()


# --- Create audit failure ---


def test_create_audit_failure_rolls_back_professional_and_audit(
    integrity_data_factory: ProfessionalIntegrityDataFactory,
) -> None:
    actor = integrity_data_factory.create_user(email_prefix="create-fail-actor")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=actor,
        name="Create Fail Clinic",
    )
    session = Session(get_engine())
    full_name = "Jordan Lee"

    try:
        service = CreateProfessionalService(
            ProfessionalRepository(session),
            session,
            _failing_recorder(),
        )
        with (
            patch.object(session, "commit", wraps=session.commit) as commit,
            patch.object(session, "rollback", wraps=session.rollback) as rollback,
            pytest.raises(SimulatedAuditRecordingError),
        ):
            service.execute(
                CreateProfessionalCommand(
                    tenant_id=tenant.id,
                    full_name=full_name,
                    specialty="Cardiology",
                    email="jordan@example.com",
                    external_reference="PROVIDER-200",
                    audit_context=_audit_context(user_id=actor.id),
                )
            )

        commit.assert_not_called()
        rollback.assert_not_called()
        assert session.in_transaction() is True
        session.rollback()
    finally:
        session.close()

    verification = Session(get_engine())
    try:
        professional = verification.execute(
            select(Professional).where(
                Professional.tenant_id == tenant.id,
                Professional.full_name == full_name,
            )
        ).scalar_one_or_none()
        audits = _professional_audit_entries(verification, tenant_id=tenant.id)
    finally:
        verification.close()

    assert professional is None
    assert audits == ()


# --- Update audit failure ---


def test_update_audit_failure_preserves_original_values_and_version(
    integrity_data_factory: ProfessionalIntegrityDataFactory,
) -> None:
    actor = integrity_data_factory.create_user(email_prefix="update-fail-actor")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=actor,
        name="Update Fail Clinic",
    )
    original = integrity_data_factory.create_professional(
        tenant=tenant,
        version=3,
        specialty="Dentistry",
        email="morgan@example.com",
        external_reference="PROVIDER-100",
    )
    session = Session(get_engine())

    try:
        service = UpdateProfessionalService(
            ProfessionalRepository(session),
            session,
            _failing_recorder(),
        )
        with (
            patch.object(session, "commit", wraps=session.commit) as commit,
            patch.object(session, "rollback", wraps=session.rollback) as rollback,
            pytest.raises(SimulatedAuditRecordingError),
        ):
            service.execute(
                UpdateProfessionalCommand(
                    tenant_id=tenant.id,
                    professional_id=original.id,
                    expected_version=original.version,
                    fields_to_update=frozenset(
                        {
                            ProfessionalMutableField.SPECIALTY,
                            ProfessionalMutableField.EMAIL,
                        }
                    ),
                    specialty="Orthodontics",
                    email="updated@example.com",
                    audit_context=_audit_context(user_id=actor.id),
                )
            )

        commit.assert_not_called()
        rollback.assert_not_called()
        session.rollback()
    finally:
        session.close()

    persisted = _fresh_professional(
        tenant_id=tenant.id,
        professional_id=original.id,
    )
    verification = Session(get_engine())
    try:
        audits = _professional_audit_entries(
            verification,
            tenant_id=tenant.id,
            resource_id=str(original.id),
        )
        update_audits = tuple(
            entry for entry in audits if entry.action == AuditAction.PROFESSIONAL_UPDATED.value
        )
    finally:
        verification.close()

    assert persisted is not None
    assert persisted.version == original.version
    assert persisted.specialty == original.specialty
    assert persisted.email == original.email
    assert persisted.external_reference == original.external_reference
    assert update_audits == ()


# --- Archive and restore audit failures ---


def test_archive_audit_failure_rolls_back_status_and_version(
    integrity_data_factory: ProfessionalIntegrityDataFactory,
) -> None:
    actor = integrity_data_factory.create_user(email_prefix="archive-fail-actor")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=actor,
        name="Archive Fail Clinic",
    )
    original = integrity_data_factory.create_professional(
        tenant=tenant,
        version=2,
        status=ProfessionalStatus.ACTIVE,
    )
    session = Session(get_engine())

    try:
        service = ArchiveProfessionalService(
            ProfessionalRepository(session),
            session,
            _failing_recorder(),
        )
        with pytest.raises(SimulatedAuditRecordingError):
            service.execute(
                ArchiveProfessionalCommand(
                    tenant_id=tenant.id,
                    professional_id=original.id,
                    expected_version=original.version,
                    audit_context=_audit_context(user_id=actor.id),
                )
            )
        session.rollback()
    finally:
        session.close()

    persisted = _fresh_professional(
        tenant_id=tenant.id,
        professional_id=original.id,
    )
    verification = Session(get_engine())
    try:
        archive_audits = tuple(
            entry
            for entry in _professional_audit_entries(
                verification,
                tenant_id=tenant.id,
                resource_id=str(original.id),
            )
            if entry.action == AuditAction.PROFESSIONAL_ARCHIVED.value
        )
    finally:
        verification.close()

    assert persisted is not None
    assert persisted.status is ProfessionalStatus.ACTIVE
    assert persisted.version == original.version
    assert archive_audits == ()


def test_restore_audit_failure_rolls_back_status_and_version(
    integrity_data_factory: ProfessionalIntegrityDataFactory,
) -> None:
    actor = integrity_data_factory.create_user(email_prefix="restore-fail-actor")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=actor,
        name="Restore Fail Clinic",
    )
    original = integrity_data_factory.create_professional(
        tenant=tenant,
        version=4,
        status=ProfessionalStatus.ARCHIVED,
    )
    session = Session(get_engine())

    try:
        service = RestoreProfessionalService(
            ProfessionalRepository(session),
            session,
            _failing_recorder(),
        )
        with pytest.raises(SimulatedAuditRecordingError):
            service.execute(
                RestoreProfessionalCommand(
                    tenant_id=tenant.id,
                    professional_id=original.id,
                    expected_version=original.version,
                    audit_context=_audit_context(user_id=actor.id),
                )
            )
        session.rollback()
    finally:
        session.close()

    persisted = _fresh_professional(
        tenant_id=tenant.id,
        professional_id=original.id,
    )
    verification = Session(get_engine())
    try:
        restore_audits = tuple(
            entry
            for entry in _professional_audit_entries(
                verification,
                tenant_id=tenant.id,
                resource_id=str(original.id),
            )
            if entry.action == AuditAction.PROFESSIONAL_RESTORED.value
        )
    finally:
        verification.close()

    assert persisted is not None
    assert persisted.status is ProfessionalStatus.ARCHIVED
    assert persisted.version == original.version
    assert restore_audits == ()


# --- Link and explicit unlink audit failures ---


def test_link_audit_failure_rolls_back_membership_and_version(
    integrity_data_factory: ProfessionalIntegrityDataFactory,
) -> None:
    actor = integrity_data_factory.create_user(email_prefix="link-fail-actor")
    member = integrity_data_factory.create_user(email_prefix="link-fail-member")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=actor,
        name="Link Fail Clinic",
    )
    membership = integrity_data_factory.add_membership(
        tenant=tenant,
        user=member,
        role=TenantRole.STAFF,
    )
    original = integrity_data_factory.create_professional(
        tenant=tenant,
        version=1,
        membership_id=None,
    )
    session = Session(get_engine())

    try:
        service = LinkProfessionalMembershipService(
            ProfessionalRepository(session),
            session,
            _failing_recorder(),
        )
        with pytest.raises(SimulatedAuditRecordingError):
            service.execute(
                LinkProfessionalMembershipCommand(
                    tenant_id=tenant.id,
                    professional_id=original.id,
                    membership_id=membership.id,
                    expected_version=original.version,
                    audit_context=_audit_context(user_id=actor.id),
                )
            )
        session.rollback()
    finally:
        session.close()

    persisted = _fresh_professional(
        tenant_id=tenant.id,
        professional_id=original.id,
    )
    membership_after = _fresh_membership(membership_id=membership.id)
    verification = Session(get_engine())
    try:
        link_audits = tuple(
            entry
            for entry in _professional_audit_entries(
                verification,
                tenant_id=tenant.id,
                resource_id=str(original.id),
            )
            if entry.action == AuditAction.PROFESSIONAL_MEMBERSHIP_LINKED.value
        )
    finally:
        verification.close()

    assert persisted is not None
    assert persisted.membership_id is None
    assert persisted.version == original.version
    assert membership_after is not None
    assert membership_after.role is membership.role
    assert membership_after.status is membership.status
    assert membership_after.user_id == membership.user_id
    assert link_audits == ()


def test_explicit_unlink_audit_failure_preserves_link_and_membership(
    integrity_data_factory: ProfessionalIntegrityDataFactory,
) -> None:
    actor = integrity_data_factory.create_user(email_prefix="unlink-fail-actor")
    member = integrity_data_factory.create_user(email_prefix="unlink-fail-member")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=actor,
        name="Unlink Fail Clinic",
    )
    membership = integrity_data_factory.add_membership(
        tenant=tenant,
        user=member,
        role=TenantRole.STAFF,
    )
    original = integrity_data_factory.create_professional(
        tenant=tenant,
        version=5,
        membership_id=membership.id,
    )
    session = Session(get_engine())

    try:
        service = UnlinkProfessionalMembershipService(
            ProfessionalRepository(session),
            session,
            _failing_recorder(),
        )
        with pytest.raises(SimulatedAuditRecordingError):
            service.execute(
                UnlinkProfessionalMembershipCommand(
                    tenant_id=tenant.id,
                    professional_id=original.id,
                    expected_version=original.version,
                    audit_context=_audit_context(user_id=actor.id),
                )
            )
        session.rollback()
    finally:
        session.close()

    persisted = _fresh_professional(
        tenant_id=tenant.id,
        professional_id=original.id,
    )
    membership_after = _fresh_membership(membership_id=membership.id)
    verification = Session(get_engine())
    try:
        unlink_audits = tuple(
            entry
            for entry in _professional_audit_entries(
                verification,
                tenant_id=tenant.id,
                resource_id=str(original.id),
            )
            if entry.action == AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value
        )
    finally:
        verification.close()

    assert persisted is not None
    assert persisted.membership_id == membership.id
    assert persisted.version == original.version
    assert membership_after is not None
    assert membership_after.role is membership.role
    assert membership_after.status is membership.status
    assert membership_after.user_id == membership.user_id
    assert unlink_audits == ()


# --- Membership-removal unlink audit failure ---


def test_membership_removal_unlink_audit_failure_prevents_deletion(
    integrity_data_factory: ProfessionalIntegrityDataFactory,
) -> None:
    owner = integrity_data_factory.create_user(email_prefix="removal-fail-owner")
    target = integrity_data_factory.create_user(email_prefix="removal-fail-target")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=owner,
        name="Removal Fail Clinic",
    )
    target_membership = integrity_data_factory.add_membership(
        tenant=tenant,
        user=target,
        role=TenantRole.STAFF,
    )
    professional = integrity_data_factory.create_professional(
        tenant=tenant,
        version=2,
        membership_id=target_membership.id,
    )
    session = Session(get_engine())
    unlink_service = UnlinkProfessionalForMembershipRemovalService(
        audit_recorder=_failing_recorder(),
    )
    service = RemoveMembershipService(
        professional_unlink_service=unlink_service,
    )

    try:
        with (
            patch.object(session, "commit", wraps=session.commit) as commit,
            patch.object(session, "rollback", wraps=session.rollback) as rollback,
            pytest.raises(SimulatedAuditRecordingError),
        ):
            service.execute(
                session,
                RemoveMembershipCommand(
                    tenant_id=tenant.id,
                    actor_user_id=owner.id,
                    membership_id=target_membership.id,
                    audit_context=_audit_context(user_id=owner.id),
                ),
            )

        commit.assert_not_called()
        rollback.assert_not_called()
        session.rollback()
    finally:
        session.close()

    membership_after = _fresh_membership(membership_id=target_membership.id)
    persisted = _fresh_professional(
        tenant_id=tenant.id,
        professional_id=professional.id,
    )
    verification = Session(get_engine())
    try:
        professional_audits = _professional_audit_entries(
            verification,
            tenant_id=tenant.id,
            resource_id=str(professional.id),
        )
        membership_audits = _membership_audit_entries(
            verification,
            tenant_id=tenant.id,
            resource_id=str(target_membership.id),
        )
        unlinked_audits = tuple(
            entry
            for entry in professional_audits
            if entry.action == AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value
        )
        removed_audits = tuple(
            entry
            for entry in membership_audits
            if entry.action == AuditAction.MEMBERSHIP_REMOVED.value
        )
    finally:
        verification.close()

    assert membership_after is not None
    assert membership_after.user_id == target.id
    assert membership_after.role is TenantRole.STAFF
    assert membership_after.status is MembershipStatus.ACTIVE
    assert persisted is not None
    assert persisted.membership_id == target_membership.id
    assert persisted.version == professional.version
    assert unlinked_audits == ()
    assert removed_audits == ()


# --- Conflict atomicity (HTTP) ---


def test_duplicate_external_reference_create_returns_409_without_audit(
    client: TestClient,
    integrity_data_factory: ProfessionalIntegrityDataFactory,
) -> None:
    owner = integrity_data_factory.create_user(email_prefix="dup-create-owner")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=owner,
        name="Dup Create Clinic",
    )
    existing = integrity_data_factory.create_professional(
        tenant=tenant,
        external_reference="PROVIDER-DUP",
        specialty="Dentistry",
        email="morgan@example.com",
    )
    headers = login_headers(client, owner)

    conflict = create_professional_via_api(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={
            "full_name": "Alex Morgan",
            "specialty": "Orthodontics",
            "email": "alex@example.com",
            "external_reference": "PROVIDER-DUP",
        },
    )
    assert_problem(
        conflict,
        status_code=409,
        code="professional_external_reference_conflict",
    )

    persisted = _fresh_professional(
        tenant_id=tenant.id,
        professional_id=existing.id,
    )
    verification = Session(get_engine())
    try:
        professionals = tuple(
            verification.execute(
                select(Professional).where(Professional.tenant_id == tenant.id)
            ).scalars()
        )
        audits = _professional_audit_entries(verification, tenant_id=tenant.id)
    finally:
        verification.close()

    assert persisted is not None
    assert persisted.external_reference == "PROVIDER-DUP"
    assert persisted.specialty == "Dentistry"
    assert persisted.email == "morgan@example.com"
    assert persisted.version == existing.version
    assert len(professionals) == 1
    assert audits == ()
    _assert_audit_metadata_safe(audits)


def test_duplicate_external_reference_update_returns_409_without_audit(
    client: TestClient,
    integrity_data_factory: ProfessionalIntegrityDataFactory,
) -> None:
    owner = integrity_data_factory.create_user(email_prefix="dup-update-owner")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=owner,
        name="Dup Update Clinic",
    )
    holder = integrity_data_factory.create_professional(
        tenant=tenant,
        full_name="Morgan Reed",
        external_reference="PROVIDER-CONFLICT",
    )
    challenger = create_professional_via_api(
        client,
        tenant_id=tenant.id,
        headers=login_headers(client, owner),
        payload={
            "full_name": "Alex Morgan",
            "specialty": "Orthodontics",
            "email": "alex@example.com",
            "phone": "+1-202-555-0199",
            "external_reference": "PROVIDER-200",
            "registration_number": "CRM-12345",
            "registration_region": "SP",
        },
    )
    assert challenger.status_code == 201
    challenger_body = challenger.json()
    challenger_id = UUID(cast(str, challenger_body["id"]))
    headers = login_headers(client, owner)

    conflict = client.patch(
        professional_path(tenant.id, challenger_id),
        headers=headers,
        json={
            "expected_version": challenger_body["version"],
            "external_reference": "PROVIDER-CONFLICT",
        },
    )
    assert_problem(
        conflict,
        status_code=409,
        code="professional_external_reference_conflict",
    )

    persisted_holder = _fresh_professional(
        tenant_id=tenant.id,
        professional_id=holder.id,
    )
    persisted_challenger = _fresh_professional(
        tenant_id=tenant.id,
        professional_id=challenger_id,
    )
    verification = Session(get_engine())
    try:
        update_audits = tuple(
            entry
            for entry in _professional_audit_entries(
                verification,
                tenant_id=tenant.id,
                resource_id=str(challenger_id),
            )
            if entry.action == AuditAction.PROFESSIONAL_UPDATED.value
        )
        all_audits = _professional_audit_entries(verification, tenant_id=tenant.id)
    finally:
        verification.close()

    assert persisted_holder is not None
    assert persisted_holder.external_reference == "PROVIDER-CONFLICT"
    assert persisted_challenger is not None
    assert persisted_challenger.external_reference == "PROVIDER-200"
    assert persisted_challenger.version == challenger_body["version"]
    assert update_audits == ()
    _assert_audit_metadata_safe(all_audits)


def test_membership_link_uniqueness_conflict_returns_409_without_partial_state(
    client: TestClient,
    integrity_data_factory: ProfessionalIntegrityDataFactory,
) -> None:
    owner = integrity_data_factory.create_user(email_prefix="link-conflict-owner")
    member = integrity_data_factory.create_user(email_prefix="link-conflict-member")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=owner,
        name="Link Conflict Clinic",
    )
    membership = integrity_data_factory.add_membership(
        tenant=tenant,
        user=member,
    )
    first = integrity_data_factory.create_professional(
        tenant=tenant,
        membership_id=membership.id,
        version=1,
        external_reference="PROVIDER-100",
    )
    second = integrity_data_factory.create_professional(
        tenant=tenant,
        full_name="Alex Morgan",
        specialty="Orthodontics",
        email="alex@example.com",
        external_reference="PROVIDER-200",
        membership_id=None,
        version=1,
    )
    headers = login_headers(client, owner)
    link_path = PROFESSIONAL_LINK_PATH.format(
        tenant_id=tenant.id,
        professional_id=second.id,
    )

    conflict = client.post(
        link_path,
        headers=headers,
        json={
            "membership_id": str(membership.id),
            "expected_version": second.version,
        },
    )
    assert_problem(
        conflict,
        status_code=409,
        code="professional_membership_link_conflict",
    )

    first_after = _fresh_professional(tenant_id=tenant.id, professional_id=first.id)
    second_after = _fresh_professional(tenant_id=tenant.id, professional_id=second.id)
    membership_after = _fresh_membership(membership_id=membership.id)
    verification = Session(get_engine())
    try:
        link_audits = tuple(
            entry
            for entry in _professional_audit_entries(verification, tenant_id=tenant.id)
            if entry.action == AuditAction.PROFESSIONAL_MEMBERSHIP_LINKED.value
        )
    finally:
        verification.close()

    assert first_after is not None
    assert first_after.membership_id == membership.id
    assert first_after.version == first.version
    assert second_after is not None
    assert second_after.membership_id is None
    assert second_after.version == second.version
    assert membership_after is not None
    assert membership_after.user_id == member.id
    assert membership_after.role is TenantRole.STAFF
    assert membership_after.status is MembershipStatus.ACTIVE
    assert link_audits == ()


# --- Stale mutation atomicity (HTTP) ---


def test_stale_update_returns_409_and_keeps_only_winner_audit(
    client: TestClient,
    integrity_data_factory: ProfessionalIntegrityDataFactory,
) -> None:
    owner = integrity_data_factory.create_user(email_prefix="stale-owner")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=owner,
        name="Stale Clinic",
    )
    headers = login_headers(client, owner)
    created = create_professional_via_api(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={
            "full_name": "Morgan Reed",
            "specialty": "Dentistry",
            "email": "morgan@example.com",
            "external_reference": "PROVIDER-100",
        },
    )
    assert created.status_code == 201
    created_body = created.json()
    professional_id = UUID(cast(str, created_body["id"]))
    item_path = professional_path(tenant.id, professional_id)

    winner = client.patch(
        item_path,
        headers=headers,
        json={
            "expected_version": 1,
            "email": "winner@example.com",
            "specialty": "Orthodontics",
        },
    )
    assert winner.status_code == 200
    assert winner.json()["version"] == 2
    assert winner.json()["email"] == "winner@example.com"

    loser = client.patch(
        item_path,
        headers=headers,
        json={
            "expected_version": 1,
            "email": "loser@example.com",
            "specialty": "Cardiology",
        },
    )
    assert_problem(
        loser,
        status_code=409,
        code="professional_version_conflict",
    )

    persisted = _fresh_professional(
        tenant_id=tenant.id,
        professional_id=professional_id,
    )
    verification = Session(get_engine())
    try:
        audits = _professional_audit_entries(
            verification,
            tenant_id=tenant.id,
            resource_id=str(professional_id),
        )
        update_audits = tuple(
            entry for entry in audits if entry.action == AuditAction.PROFESSIONAL_UPDATED.value
        )
    finally:
        verification.close()

    assert persisted is not None
    assert persisted.version == 2
    assert persisted.email == "winner@example.com"
    assert persisted.specialty == "Orthodontics"
    assert len(update_audits) == 1
    assert update_audits[0].event_metadata == {
        "version": 2,
        "changed_fields": ["specialty", "email"],
    }
    _assert_audit_metadata_safe(audits)


# --- Caller ownership ---


def test_services_never_commit_or_rollback_on_success(
    integrity_data_factory: ProfessionalIntegrityDataFactory,
) -> None:
    actor = integrity_data_factory.create_user(email_prefix="ownership-actor")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=actor,
        name="Ownership Clinic",
    )
    session = Session(get_engine())

    try:
        with (
            patch.object(session, "commit", wraps=session.commit) as commit,
            patch.object(session, "rollback", wraps=session.rollback) as rollback,
        ):
            created = CreateProfessionalService(
                ProfessionalRepository(session),
                session,
            ).execute(
                CreateProfessionalCommand(
                    tenant_id=tenant.id,
                    full_name="Morgan Reed",
                    specialty="Dentistry",
                    audit_context=_audit_context(user_id=actor.id),
                )
            )
            UpdateProfessionalService(
                ProfessionalRepository(session),
                session,
            ).execute(
                UpdateProfessionalCommand(
                    tenant_id=tenant.id,
                    professional_id=created.professional.id,
                    expected_version=created.professional.version,
                    fields_to_update=frozenset({ProfessionalMutableField.EMAIL}),
                    email="updated@example.com",
                    audit_context=_audit_context(user_id=actor.id),
                )
            )

        commit.assert_not_called()
        rollback.assert_not_called()
        assert session.in_transaction() is True
        session.rollback()
    finally:
        session.close()


def test_successful_api_mutation_commits_once_through_route(
    client: TestClient,
    integrity_data_factory: ProfessionalIntegrityDataFactory,
    app_settings: Settings,
) -> None:
    owner = integrity_data_factory.create_user(email_prefix="commit-once-owner")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=owner,
        name="Commit Once Clinic",
    )
    headers = login_headers(client, owner)
    commit_log: list[str] = []
    app = create_app(app_settings)

    def override() -> Iterator[Session]:
        yield from _tracked_session_override(commit_log)

    app.dependency_overrides[get_database_session] = override

    with TestClient(app) as tracked_client:
        response = create_professional_via_api(
            tracked_client,
            tenant_id=tenant.id,
            headers=headers,
            payload={"external_reference": "PROVIDER-200"},
        )

    assert response.status_code == 201
    assert commit_log.count("commit") == 1


def test_get_routes_do_not_commit(
    client: TestClient,
    integrity_data_factory: ProfessionalIntegrityDataFactory,
    app_settings: Settings,
) -> None:
    owner = integrity_data_factory.create_user(email_prefix="get-no-commit-owner")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=owner,
        name="Get No Commit Clinic",
    )
    professional = integrity_data_factory.create_professional(tenant=tenant)
    headers = login_headers(client, owner)
    commit_log: list[str] = []
    app = create_app(app_settings)

    def override() -> Iterator[Session]:
        yield from _tracked_session_override(commit_log)

    app.dependency_overrides[get_database_session] = override

    with TestClient(app) as tracked_client:
        list_response = tracked_client.get(
            professionals_collection_path(tenant.id),
            headers=headers,
        )
        get_response = tracked_client.get(
            professional_path(tenant.id, professional.id),
            headers=headers,
        )

    assert list_response.status_code == 200
    assert get_response.status_code == 200
    assert commit_log.count("commit") == 0


def test_service_exception_prevents_route_commit(
    client: TestClient,
    integrity_data_factory: ProfessionalIntegrityDataFactory,
    app_settings: Settings,
) -> None:
    owner = integrity_data_factory.create_user(email_prefix="no-commit-fail-owner")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=owner,
        name="No Commit Fail Clinic",
    )
    integrity_data_factory.create_professional(
        tenant=tenant,
        external_reference="PROVIDER-DUP",
    )
    headers = login_headers(client, owner)
    commit_log: list[str] = []
    app = create_app(app_settings)

    def override() -> Iterator[Session]:
        yield from _tracked_session_override(commit_log)

    app.dependency_overrides[get_database_session] = override

    with TestClient(app) as tracked_client:
        response = create_professional_via_api(
            tracked_client,
            tenant_id=tenant.id,
            headers=headers,
            payload={
                "full_name": "Alex Morgan",
                "external_reference": "PROVIDER-DUP",
            },
        )

    assert_problem(
        response,
        status_code=409,
        code="professional_external_reference_conflict",
    )
    assert commit_log.count("commit") == 0


# --- Audit safety ---


def test_persisted_professional_audit_metadata_excludes_profile_fields(
    client: TestClient,
    integrity_data_factory: ProfessionalIntegrityDataFactory,
) -> None:
    owner = integrity_data_factory.create_user(email_prefix="audit-safe-owner")
    member = integrity_data_factory.create_user(email_prefix="audit-safe-member")
    tenant, _ = integrity_data_factory.create_tenant(
        owner=owner,
        name="Audit Safe Clinic",
    )
    membership = integrity_data_factory.add_membership(
        tenant=tenant,
        user=member,
    )
    headers = login_headers(client, owner)

    created = create_professional_via_api(
        client,
        tenant_id=tenant.id,
        headers=headers,
        payload={
            "full_name": "Morgan Reed",
            "specialty": "Dentistry",
            "registration_number": "DDS-48291",
            "registration_region": "CA",
            "email": "morgan@example.com",
            "phone": "+1-202-555-0130",
            "external_reference": "PROVIDER-100",
        },
    )
    assert created.status_code == 201
    body = created.json()
    professional_id = body["id"]
    item_path = professional_path(tenant.id, professional_id)

    patched = client.patch(
        item_path,
        headers=headers,
        json={
            "expected_version": 1,
            "specialty": "Orthodontics",
            "email": "updated@example.com",
        },
    )
    assert patched.status_code == 200

    archived = client.post(
        f"/api/v1/tenants/{tenant.id}/professionals/{professional_id}/archive",
        headers=headers,
        json={"expected_version": 2},
    )
    assert archived.status_code == 200

    restored = client.post(
        f"/api/v1/tenants/{tenant.id}/professionals/{professional_id}/restore",
        headers=headers,
        json={"expected_version": 3},
    )
    assert restored.status_code == 200

    linked = client.post(
        PROFESSIONAL_LINK_PATH.format(
            tenant_id=tenant.id,
            professional_id=professional_id,
        ),
        headers=headers,
        json={
            "membership_id": str(membership.id),
            "expected_version": 4,
        },
    )
    assert linked.status_code == 200

    unlinked = client.post(
        (f"/api/v1/tenants/{tenant.id}/professionals/{professional_id}/unlink-membership"),
        headers=headers,
        json={"expected_version": 5},
    )
    assert unlinked.status_code == 200

    verification = Session(get_engine())
    try:
        entries = _professional_audit_entries(
            verification,
            tenant_id=tenant.id,
            resource_id=str(professional_id),
        )
    finally:
        verification.close()

    assert {entry.action for entry in entries} == {
        AuditAction.PROFESSIONAL_CREATED.value,
        AuditAction.PROFESSIONAL_UPDATED.value,
        AuditAction.PROFESSIONAL_ARCHIVED.value,
        AuditAction.PROFESSIONAL_RESTORED.value,
        AuditAction.PROFESSIONAL_MEMBERSHIP_LINKED.value,
        AuditAction.PROFESSIONAL_MEMBERSHIP_UNLINKED.value,
    }
    _assert_audit_metadata_safe(entries)
