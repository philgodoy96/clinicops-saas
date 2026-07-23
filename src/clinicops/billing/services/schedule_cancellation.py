from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import RecordAuditLogCommand
from clinicops.audit.recording import AuditRecorder, SqlAlchemyAuditRecorder
from clinicops.billing.enums import (
    ProviderOperationStatus,
    ProviderOperationType,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingIdempotencyConflictError,
    BillingSubscriptionAlreadyCanceledError,
    BillingSubscriptionCancellationPendingError,
    BillingSubscriptionNotActiveError,
    BillingSubscriptionNotFoundError,
    ProviderOperationAlreadyExistsError,
    ProviderOperationInProgressError,
)
from clinicops.billing.fingerprints import (
    fingerprint_billing_command,
)
from clinicops.billing.idempotency_keys import (
    validate_idempotency_key,
)
from clinicops.billing.models import (
    ProviderOperation,
    Subscription,
)
from clinicops.billing.providers.base import PaymentProvider
from clinicops.billing.providers.contracts import (
    CancelSubscriptionRequest,
    CancelSubscriptionResult,
)
from clinicops.billing.providers.exceptions import (
    ProviderRetryableError,
    ProviderTerminalError,
)
from clinicops.billing.providers.idempotency import (
    build_provider_operation_key,
)
from clinicops.billing.repositories.provider_operation_repository import (
    ProviderOperationRepository,
)
from clinicops.billing.repositories.subscription_repository import (
    SubscriptionRepository,
)
from clinicops.billing.services.get_subscription import (
    BillingSubscriptionDetails,
)
from clinicops.core.clock import Clock, SystemClock


@dataclass(frozen=True, slots=True)
class ScheduleBillingSubscriptionCancellationCommand:
    """Validated input for one period-end subscription cancellation."""

    tenant_id: UUID
    idempotency_key: str
    audit_context: AuditRecordingContext

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "idempotency_key",
            validate_idempotency_key(self.idempotency_key),
        )


@dataclass(frozen=True, slots=True)
class ScheduledBillingSubscriptionCancellation(BillingSubscriptionDetails):
    """Persisted subscription state after scheduling cancellation."""

    replayed: bool


@dataclass(frozen=True, slots=True)
class _ReservedCancellation:
    operation_id: UUID | None
    provider_subscription_id: str | None
    effective_at: datetime | None
    replayed_result: ScheduledBillingSubscriptionCancellation | None


class ScheduleBillingSubscriptionCancellationService:
    """Coordinate a durable period-end subscription cancellation."""

    def __init__(
        self,
        payment_provider: PaymentProvider,
        *,
        subscription_repository: (SubscriptionRepository | None) = None,
        provider_operation_repository: (ProviderOperationRepository | None) = None,
        clock: Clock | None = None,
        audit_recorder: AuditRecorder | None = None,
    ) -> None:
        self._payment_provider = payment_provider
        self._subscription_repository = (
            subscription_repository
            if subscription_repository is not None
            else SubscriptionRepository()
        )
        self._provider_operation_repository = (
            provider_operation_repository
            if provider_operation_repository is not None
            else ProviderOperationRepository()
        )
        self._clock = clock if clock is not None else SystemClock()
        self._audit_recorder = (
            audit_recorder if audit_recorder is not None else SqlAlchemyAuditRecorder()
        )

    def execute(
        self,
        session: Session,
        command: (ScheduleBillingSubscriptionCancellationCommand),
    ) -> ScheduledBillingSubscriptionCancellation:
        """Schedule or replay one tenant cancellation workflow.

        This orchestrator intentionally owns multiple commits. The provider
        call occurs only after the durable operation claim has committed.
        The final local cancellation mutation and audit entry are left for the
        caller transaction owner to commit.
        """

        reservation = self._reserve_with_conflict_recovery(
            session,
            command,
        )

        if reservation.replayed_result is not None:
            return reservation.replayed_result

        session.commit()
        self._require_no_open_transaction(session)

        operation_id = _require_uuid(
            reservation.operation_id,
            field_name="operation_id",
        )
        provider_subscription_id = _require_string(
            reservation.provider_subscription_id,
            field_name="provider_subscription_id",
        )
        effective_at = _require_datetime(
            reservation.effective_at,
            field_name="effective_at",
        )

        try:
            provider_result = self._payment_provider.cancel_subscription(
                CancelSubscriptionRequest(
                    provider_operation_key=(build_provider_operation_key(operation_id)),
                    provider_subscription_id=(provider_subscription_id),
                    effective_at=effective_at,
                )
            )
        except ProviderRetryableError as error:
            self._persist_provider_failure(
                session=session,
                operation_id=operation_id,
                error=error,
            )
            raise
        except ProviderTerminalError as error:
            self._persist_provider_failure(
                session=session,
                operation_id=operation_id,
                error=error,
            )
            raise

        try:
            return self._apply_provider_result(
                session=session,
                command=command,
                operation_id=operation_id,
                result=provider_result,
            )
        except (
            BillingSubscriptionAlreadyCanceledError,
            BillingSubscriptionCancellationPendingError,
            BillingSubscriptionNotActiveError,
        ) as error:
            self._rollback_if_active(session)
            self._persist_application_failure(
                session=session,
                operation_id=operation_id,
                failure_code=error.code,
                failure_message=error.public_message,
            )
            raise
        except Exception:
            self._rollback_if_active(session)
            raise

    def _reserve_with_conflict_recovery(
        self,
        session: Session,
        command: (ScheduleBillingSubscriptionCancellationCommand),
    ) -> _ReservedCancellation:
        try:
            return self._reserve_or_replay(
                session,
                command,
            )
        except ProviderOperationAlreadyExistsError:
            session.rollback()
            return self._reserve_or_replay(
                session,
                command,
            )

    def _reserve_or_replay(
        self,
        session: Session,
        command: (ScheduleBillingSubscriptionCancellationCommand),
    ) -> _ReservedCancellation:
        subscription = self._subscription_repository.get_by_tenant_id_for_update(
            session,
            tenant_id=command.tenant_id,
        )
        if subscription is None:
            raise BillingSubscriptionNotFoundError()

        provider_subscription_id = _require_string(
            subscription.provider_subscription_id,
            field_name="provider_subscription_id",
        )
        effective_at = _require_datetime(
            subscription.current_period_end,
            field_name="current_period_end",
        )
        request_fingerprint = fingerprint_billing_command(
            operation_type=(ProviderOperationType.CANCEL_SUBSCRIPTION),
            fields={
                "provider_subscription_id": (provider_subscription_id),
                "effective_at": (effective_at.astimezone(UTC).isoformat()),
            },
        )
        operation = self._provider_operation_repository.get_by_idempotency_key_for_update(
            session,
            tenant_id=command.tenant_id,
            operation_type=(ProviderOperationType.CANCEL_SUBSCRIPTION),
            idempotency_key=command.idempotency_key,
        )

        if operation is not None:
            self._require_matching_fingerprint(
                operation=operation,
                request_fingerprint=request_fingerprint,
            )

            if operation.status is ProviderOperationStatus.SUCCEEDED:
                if not subscription.cancel_at_period_end:
                    raise RuntimeError(
                        "Succeeded cancellation operation has no matching local cancellation state."
                    )

                if subscription.cancellation_requested_at is None:
                    raise RuntimeError(
                        "Succeeded cancellation operation has no local request timestamp."
                    )

                if subscription.canceled_at is not None:
                    raise RuntimeError(
                        "Scheduled cancellation replay found an already-finalized subscription."
                    )

                return _ReservedCancellation(
                    operation_id=None,
                    provider_subscription_id=None,
                    effective_at=None,
                    replayed_result=self._to_result(
                        subscription,
                        replayed=True,
                    ),
                )

            self._raise_if_operation_cannot_resume(operation)

        self._validate_subscription_for_cancellation(subscription)

        if operation is None:
            operation = ProviderOperation(
                id=uuid4(),
                tenant_id=command.tenant_id,
                billing_customer_id=(subscription.billing_customer_id),
                subscription_id=subscription.id,
                provider=subscription.provider,
                operation_type=(ProviderOperationType.CANCEL_SUBSCRIPTION),
                idempotency_key=command.idempotency_key,
                request_fingerprint=request_fingerprint,
                request_payload={
                    "provider_subscription_id": (provider_subscription_id),
                    "effective_at": (effective_at.astimezone(UTC).isoformat()),
                },
                status=ProviderOperationStatus.PENDING,
                attempt_count=0,
                provider_reference=None,
                result_payload=None,
                failure_code=None,
                failure_message=None,
                started_at=None,
                completed_at=None,
            )
            self._provider_operation_repository.add_and_flush(
                session,
                operation,
            )

        self._claim_operation(
            operation,
            now=self._clock.now(),
        )
        self._provider_operation_repository.flush(
            session,
        )

        return _ReservedCancellation(
            operation_id=operation.id,
            provider_subscription_id=(provider_subscription_id),
            effective_at=effective_at,
            replayed_result=None,
        )

    @staticmethod
    def _validate_subscription_for_cancellation(
        subscription: Subscription,
    ) -> None:
        if (
            subscription.canceled_at is not None
            or subscription.status is SubscriptionStatus.CANCELED
        ):
            raise BillingSubscriptionAlreadyCanceledError()

        if subscription.status is not SubscriptionStatus.ACTIVE:
            raise BillingSubscriptionNotActiveError()

        if subscription.cancel_at_period_end:
            raise BillingSubscriptionCancellationPendingError()

    def _apply_provider_result(
        self,
        *,
        session: Session,
        command: ScheduleBillingSubscriptionCancellationCommand,
        operation_id: UUID,
        result: CancelSubscriptionResult,
    ) -> ScheduledBillingSubscriptionCancellation:
        # Lock order must match _reserve_or_replay (subscription then
        # operation) so a concurrent reserve cannot deadlock with apply.
        subscription = self._subscription_repository.get_by_tenant_id_for_update(
            session,
            tenant_id=command.tenant_id,
        )
        if subscription is None:
            raise RuntimeError("Reserved cancellation operation has no persisted subscription.")

        operation = self._require_operation_for_update(
            session,
            operation_id,
        )
        self._require_in_progress(operation)

        if operation.subscription_id != subscription.id:
            raise RuntimeError("Reserved cancellation operation references another subscription.")

        self._validate_subscription_for_cancellation(subscription)
        provider_subscription_id = _require_string(
            subscription.provider_subscription_id,
            field_name="provider_subscription_id",
        )
        effective_at = _require_datetime(
            subscription.current_period_end,
            field_name="current_period_end",
        )

        if result.provider_subscription_id != provider_subscription_id:
            raise RuntimeError("Provider cancellation result references another subscription.")

        if result.canceled_at != effective_at:
            raise RuntimeError(
                "Provider cancellation result confirmed another effective period boundary."
            )

        if result.provider_state_version <= subscription.provider_state_version:
            raise RuntimeError(
                "Provider cancellation result did not advance the provider state version."
            )

        previous_status = subscription.status
        requested_at = self._clock.now()
        subscription.cancel_at_period_end = True
        subscription.cancellation_requested_at = requested_at
        subscription.pending_price_code = None
        subscription.provider_state_version = result.provider_state_version
        self._subscription_repository.flush(session)

        operation.subscription_id = subscription.id
        self._mark_operation_succeeded(
            operation=operation,
            provider_reference=result.provider_reference,
            result_payload={
                "provider_subscription_id": (result.provider_subscription_id),
                "provider_state_version": (result.provider_state_version),
                "canceled_at": (result.canceled_at.astimezone(UTC).isoformat()),
                "provider_reference": (result.provider_reference),
            },
            completed_at=requested_at,
        )
        self._provider_operation_repository.flush(
            session,
        )

        audit_context = command.audit_context
        self._audit_recorder.record(
            session,
            RecordAuditLogCommand(
                tenant_id=subscription.tenant_id,
                actor=audit_context.actor,
                source=audit_context.source,
                action=AuditAction.BILLING_SUBSCRIPTION_CANCELLED.value,
                resource_type=AuditResourceType.SUBSCRIPTION.value,
                resource_id=str(subscription.id),
                correlation_id=audit_context.correlation_id,
                metadata_version=1,
                metadata={
                    "previous_status": previous_status.value,
                    "new_status": SubscriptionStatus.CANCELED.value,
                },
                # Domain commands already carry a durable client idempotency key
                # used for provider-operation replay protection.
                idempotency_key=(
                    f"subscription-cancelled:{subscription.id}:{command.idempotency_key}"
                ),
                request_id=audit_context.request_id,
            ),
        )

        return self._to_result(
            subscription,
            replayed=False,
        )

    def _persist_provider_failure(
        self,
        *,
        session: Session,
        operation_id: UUID,
        error: ProviderRetryableError | ProviderTerminalError,
    ) -> None:
        self._rollback_if_active(session)
        operation = self._require_operation_for_update(
            session,
            operation_id,
        )
        self._require_in_progress(operation)

        if error.retryable:
            operation.status = ProviderOperationStatus.FAILED_RETRYABLE
            operation.completed_at = None
        else:
            operation.status = ProviderOperationStatus.FAILED_TERMINAL
            operation.completed_at = self._clock.now()

        operation.failure_code = error.code
        operation.failure_message = error.public_message[:512]
        operation.provider_reference = None
        operation.result_payload = None
        self._provider_operation_repository.flush(
            session,
        )
        session.commit()

    def _persist_application_failure(
        self,
        *,
        session: Session,
        operation_id: UUID,
        failure_code: str,
        failure_message: str,
    ) -> None:
        operation = self._require_operation_for_update(
            session,
            operation_id,
        )
        self._require_in_progress(operation)
        operation.status = ProviderOperationStatus.FAILED_TERMINAL
        operation.completed_at = self._clock.now()
        operation.failure_code = failure_code
        operation.failure_message = failure_message[:512]
        operation.provider_reference = None
        operation.result_payload = None
        self._provider_operation_repository.flush(
            session,
        )
        session.commit()

    def _require_operation_for_update(
        self,
        session: Session,
        operation_id: UUID,
    ) -> ProviderOperation:
        operation = self._provider_operation_repository.get_by_id_for_update(
            session,
            provider_operation_id=operation_id,
        )
        if operation is None:
            raise RuntimeError("Reserved provider operation was not found.")

        return operation

    @staticmethod
    def _require_matching_fingerprint(
        *,
        operation: ProviderOperation,
        request_fingerprint: str,
    ) -> None:
        if operation.request_fingerprint != request_fingerprint:
            raise BillingIdempotencyConflictError()

    @staticmethod
    def _raise_if_operation_cannot_resume(
        operation: ProviderOperation,
    ) -> None:
        if operation.status is ProviderOperationStatus.IN_PROGRESS:
            raise ProviderOperationInProgressError()

        if operation.status is ProviderOperationStatus.FAILED_TERMINAL:
            raise ProviderTerminalError(
                provider=operation.provider,
                operation_type=operation.operation_type,
                internal_message=(
                    operation.failure_message or "Stored provider operation failed terminally."
                ),
            )

    @staticmethod
    def _claim_operation(
        operation: ProviderOperation,
        *,
        now: datetime,
    ) -> None:
        if operation.status not in {
            ProviderOperationStatus.PENDING,
            ProviderOperationStatus.FAILED_RETRYABLE,
        }:
            raise RuntimeError(
                f"Provider operation cannot be claimed from status {operation.status.value}."
            )

        operation.status = ProviderOperationStatus.IN_PROGRESS
        operation.attempt_count += 1
        operation.started_at = now
        operation.completed_at = None
        operation.provider_reference = None
        operation.result_payload = None
        operation.failure_code = None
        operation.failure_message = None

    @staticmethod
    def _mark_operation_succeeded(
        *,
        operation: ProviderOperation,
        provider_reference: str,
        result_payload: dict[str, object],
        completed_at: datetime,
    ) -> None:
        ScheduleBillingSubscriptionCancellationService._require_in_progress(operation)
        operation.status = ProviderOperationStatus.SUCCEEDED
        operation.provider_reference = provider_reference
        operation.result_payload = result_payload
        operation.failure_code = None
        operation.failure_message = None
        operation.completed_at = completed_at

    @staticmethod
    def _require_in_progress(
        operation: ProviderOperation,
    ) -> None:
        if operation.status is not ProviderOperationStatus.IN_PROGRESS:
            raise RuntimeError("Provider operation is not in progress.")

    @staticmethod
    def _require_no_open_transaction(
        session: Session,
    ) -> None:
        if session.in_transaction():
            raise RuntimeError("A database transaction is open during a payment-provider call.")

    @staticmethod
    def _rollback_if_active(session: Session) -> None:
        if session.in_transaction():
            session.rollback()

    @staticmethod
    def _to_result(
        subscription: Subscription,
        *,
        replayed: bool,
    ) -> ScheduledBillingSubscriptionCancellation:
        return ScheduledBillingSubscriptionCancellation(
            id=subscription.id,
            tenant_id=subscription.tenant_id,
            price_code=subscription.price_code,
            plan=subscription.plan,
            billing_interval=subscription.billing_interval,
            currency=subscription.currency,
            unit_amount=subscription.unit_amount,
            status=subscription.status,
            current_period_start=_require_datetime(
                subscription.current_period_start,
                field_name="current_period_start",
            ),
            current_period_end=_require_datetime(
                subscription.current_period_end,
                field_name="current_period_end",
            ),
            cancel_at_period_end=(subscription.cancel_at_period_end),
            cancellation_requested_at=(subscription.cancellation_requested_at),
            canceled_at=subscription.canceled_at,
            pending_price_code=(subscription.pending_price_code),
            created_at=_require_datetime(
                subscription.created_at,
                field_name="created_at",
            ),
            updated_at=_require_datetime(
                subscription.updated_at,
                field_name="updated_at",
            ),
            replayed=replayed,
        )


def _require_uuid(
    value: UUID | None,
    *,
    field_name: str,
) -> UUID:
    if value is None:
        raise RuntimeError(f"{field_name} is required for a billing cancellation.")

    return value


def _require_string(
    value: str | None,
    *,
    field_name: str,
) -> str:
    if value is None or not value.strip():
        raise RuntimeError(f"{field_name} is required for a billing cancellation.")

    return value.strip()


def _require_datetime(
    value: datetime | None,
    *,
    field_name: str,
) -> datetime:
    if value is None:
        raise RuntimeError(f"{field_name} is required for a billing cancellation.")

    if value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError(f"{field_name} must be timezone-aware.")

    return value.astimezone(UTC)
