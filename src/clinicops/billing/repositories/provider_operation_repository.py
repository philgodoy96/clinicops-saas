from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clinicops.billing.enums import ProviderOperationType
from clinicops.billing.exceptions import (
    ProviderOperationAlreadyExistsError,
)
from clinicops.billing.models import ProviderOperation

_IDEMPOTENCY_CONSTRAINT = "uq_provider_operations_tenant_op_idempotency"


class ProviderOperationRepository:
    """Persist and lock durable outbound provider operations."""

    def get_by_idempotency_key(
        self,
        session: Session,
        *,
        tenant_id: UUID,
        operation_type: ProviderOperationType,
        idempotency_key: str,
    ) -> ProviderOperation | None:
        statement = select(ProviderOperation).where(
            ProviderOperation.tenant_id == tenant_id,
            ProviderOperation.operation_type == operation_type,
            ProviderOperation.idempotency_key == idempotency_key,
        )

        return session.scalar(statement)

    def get_by_idempotency_key_for_update(
        self,
        session: Session,
        *,
        tenant_id: UUID,
        operation_type: ProviderOperationType,
        idempotency_key: str,
    ) -> ProviderOperation | None:
        statement = (
            select(ProviderOperation)
            .where(
                ProviderOperation.tenant_id == tenant_id,
                ProviderOperation.operation_type == operation_type,
                ProviderOperation.idempotency_key == idempotency_key,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return session.scalar(statement)

    def get_by_id_for_update(
        self,
        session: Session,
        *,
        provider_operation_id: UUID,
    ) -> ProviderOperation | None:
        statement = (
            select(ProviderOperation)
            .where(ProviderOperation.id == provider_operation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return session.scalar(statement)

    def add_and_flush(
        self,
        session: Session,
        operation: ProviderOperation,
    ) -> ProviderOperation:
        session.add(operation)

        try:
            session.flush()
        except IntegrityError as error:
            if _constraint_name(error) == _IDEMPOTENCY_CONSTRAINT:
                raise ProviderOperationAlreadyExistsError() from error

            raise

        return operation

    def flush(self, session: Session) -> None:
        session.flush()


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)
