from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from clinicops.audit.actions import AuditAction, AuditResourceType
from clinicops.audit.context import AuditRecordingContext
from clinicops.audit.contracts import RecordAuditLogCommand
from clinicops.audit.recording import AuditRecorder, SqlAlchemyAuditRecorder
from clinicops.billing.catalog import get_price_definition
from clinicops.billing.enums import (
    BillingInterval,
    BillingPlan,
    ProviderOperationStatus,
    ProviderOperationType,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingCustomerAlreadyExistsError,
    BillingIdempotencyConflictError,
    BillingSubscriptionAlreadyExistsError,
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
    BillingCustomer,
    ProviderOperation,
    Subscription,
)
from clinicops.billing.providers.base import PaymentProvider
from clinicops.billing.providers.contracts import (
    CreateCustomerRequest,
    CreateCustomerResult,
    CreateSubscriptionRequest,
    CreateSubscriptionResult,
)
from clinicops.billing.providers.exceptions import (
    ProviderRetryableError,
    ProviderTerminalError,
)
from clinicops.billing.providers.idempotency import (
    build_provider_operation_key,
)
from clinicops.billing.repositories.billing_customer_repository import (
    BillingCustomerRepository,
)
from clinicops.billing.repositories.provider_operation_repository import (
    ProviderOperationRepository,
)
from clinicops.billing.repositories.subscription_repository import (
    SubscriptionRepository,
)
from clinicops.core.clock import Clock, SystemClock


@dataclass(frozen=True, slots=True)
class CreateBillingSubscriptionCommand:
    """Validated input for the tenant subscription-creation workflow."""

    tenant_id: UUID
    price_code: str
    idempotency_key: str
    audit_context: AuditRecordingContext

    def __post_init__(self) -> None:
        normalized_price_code = self.price_code.strip()
        price = get_price_definition(normalized_price_code)

        object.__setattr__(
            self,
            "price_code",
            price.price_code,
        )
        object.__setattr__(
            self,
            "idempotency_key",
            validate_idempotency_key(self.idempotency_key),
        )


@dataclass(frozen=True, slots=True)
class CreatedBillingSubscription:
    """Public application result for a created or replayed subscription."""

    id: UUID
    tenant_id: UUID
    price_code: str
    plan: BillingPlan
    billing_interval: BillingInterval
    currency: str
    unit_amount: int
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    cancellation_requested_at: datetime | None
    canceled_at: datetime | None
    pending_price_code: str | None
    created_at: datetime
    updated_at: datetime
    replayed: bool

    def __post_init__(self) -> None:
        normalized_price_code = self.price_code.strip()
        normalized_currency = self.currency.strip().upper()

        if not normalized_price_code:
            raise ValueError("price_code must not be empty.")

        if not normalized_currency:
            raise ValueError("currency must not be empty.")

        if self.unit_amount <= 0:
            raise ValueError("unit_amount must be greater than zero.")

        period_start = _normalize_datetime(
            self.current_period_start,
            field_name="current_period_start",
        )
        period_end = _normalize_datetime(
            self.current_period_end,
            field_name="current_period_end",
        )

        if period_start >= period_end:
            raise ValueError("current_period_start must be earlier than current_period_end.")

        cancellation_requested_at = _normalize_optional_datetime(
            self.cancellation_requested_at,
            field_name="cancellation_requested_at",
        )
        canceled_at = _normalize_optional_datetime(
            self.canceled_at,
            field_name="canceled_at",
        )
        created_at = _normalize_datetime(
            self.created_at,
            field_name="created_at",
        )
        updated_at = _normalize_datetime(
            self.updated_at,
            field_name="updated_at",
        )

        if updated_at < created_at:
            raise ValueError("updated_at must not be earlier than created_at.")

        pending_price_code = (
            self.pending_price_code.strip() if self.pending_price_code is not None else None
        )

        if pending_price_code == "":
            raise ValueError("pending_price_code must not be empty.")

        object.__setattr__(
            self,
            "price_code",
            normalized_price_code,
        )
        object.__setattr__(
            self,
            "currency",
            normalized_currency,
        )
        object.__setattr__(
            self,
            "current_period_start",
            period_start,
        )
        object.__setattr__(
            self,
            "current_period_end",
            period_end,
        )
        object.__setattr__(
            self,
            "cancellation_requested_at",
            cancellation_requested_at,
        )
        object.__setattr__(
            self,
            "canceled_at",
            canceled_at,
        )
        object.__setattr__(
            self,
            "pending_price_code",
            pending_price_code,
        )
        object.__setattr__(
            self,
            "created_at",
            created_at,
        )
        object.__setattr__(
            self,
            "updated_at",
            updated_at,
        )


@dataclass(frozen=True, slots=True)
class _ReservedSubscriptionCreation:
    subscription_operation_id: UUID | None
    customer_operation_id: UUID | None
    provider_customer_id: str | None
    replayed_result: CreatedBillingSubscription | None


class CreateBillingSubscriptionService:
    """Coordinate durable subscription creation across provider calls."""

    def __init__(
        self,
        payment_provider: PaymentProvider,
        *,
        billing_customer_repository: (BillingCustomerRepository | None) = None,
        subscription_repository: (SubscriptionRepository | None) = None,
        provider_operation_repository: (ProviderOperationRepository | None) = None,
        clock: Clock | None = None,
        audit_recorder: AuditRecorder | None = None,
    ) -> None:
        self._payment_provider = payment_provider
        self._billing_customer_repository = (
            billing_customer_repository
            if billing_customer_repository is not None
            else BillingCustomerRepository()
        )
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
        command: CreateBillingSubscriptionCommand,
    ) -> CreatedBillingSubscription:
        """Create or replay one tenant subscription workflow.

        This orchestrator intentionally owns multiple commits. Each provider
        call occurs only after the preceding database transaction commits.
        The final local subscription mutation and audit entry are left for the
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

        provider_customer_id = reservation.provider_customer_id

        if reservation.customer_operation_id is not None:
            provider_customer_id = self._execute_customer_creation(
                session=session,
                command=command,
                customer_operation_id=(reservation.customer_operation_id),
                subscription_operation_id=(
                    _require_uuid(
                        reservation.subscription_operation_id,
                        field_name=("subscription_operation_id"),
                    )
                ),
            )

        subscription_operation_id = _require_uuid(
            reservation.subscription_operation_id,
            field_name="subscription_operation_id",
        )
        confirmed_provider_customer_id = _require_string(
            provider_customer_id,
            field_name="provider_customer_id",
        )

        return self._execute_subscription_creation(
            session=session,
            command=command,
            subscription_operation_id=(subscription_operation_id),
            provider_customer_id=(confirmed_provider_customer_id),
        )

    def _reserve_with_conflict_recovery(
        self,
        session: Session,
        command: CreateBillingSubscriptionCommand,
    ) -> _ReservedSubscriptionCreation:
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
        command: CreateBillingSubscriptionCommand,
    ) -> _ReservedSubscriptionCreation:
        now = self._clock.now()
        subscription_fingerprint = fingerprint_billing_command(
            operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
            fields={
                "price_code": command.price_code,
            },
        )
        subscription_operation = (
            self._provider_operation_repository.get_by_idempotency_key_for_update(
                session,
                tenant_id=command.tenant_id,
                operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
                idempotency_key=command.idempotency_key,
            )
        )

        if subscription_operation is not None:
            self._require_matching_fingerprint(
                operation=subscription_operation,
                request_fingerprint=(subscription_fingerprint),
            )

            if subscription_operation.status is ProviderOperationStatus.SUCCEEDED:
                subscription = self._subscription_repository.get_by_tenant_id_for_update(
                    session,
                    tenant_id=command.tenant_id,
                )
                if subscription is None:
                    raise RuntimeError(
                        "Succeeded subscription operation has no persisted subscription."
                    )

                return _ReservedSubscriptionCreation(
                    subscription_operation_id=None,
                    customer_operation_id=None,
                    provider_customer_id=None,
                    replayed_result=self._to_result(
                        subscription,
                        replayed=True,
                    ),
                )

            self._raise_if_operation_cannot_resume(subscription_operation)

        existing_subscription = self._subscription_repository.get_by_tenant_id_for_update(
            session,
            tenant_id=command.tenant_id,
        )
        if existing_subscription is not None:
            raise BillingSubscriptionAlreadyExistsError()

        billing_customer = self._billing_customer_repository.get_by_tenant_and_provider_for_update(
            session,
            tenant_id=command.tenant_id,
            provider=self._payment_provider.provider,
        )

        if subscription_operation is None:
            subscription_operation = ProviderOperation(
                id=uuid4(),
                tenant_id=command.tenant_id,
                billing_customer_id=(billing_customer.id if billing_customer is not None else None),
                subscription_id=None,
                provider=self._payment_provider.provider,
                operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
                idempotency_key=command.idempotency_key,
                request_fingerprint=(subscription_fingerprint),
                request_payload={
                    "price_code": command.price_code,
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
                subscription_operation,
            )

        if billing_customer is not None and billing_customer.provider_customer_id is not None:
            self._claim_operation(
                subscription_operation,
                now=now,
            )
            self._provider_operation_repository.flush(
                session,
            )

            return _ReservedSubscriptionCreation(
                subscription_operation_id=(subscription_operation.id),
                customer_operation_id=None,
                provider_customer_id=(billing_customer.provider_customer_id),
                replayed_result=None,
            )

        customer_fingerprint = fingerprint_billing_command(
            operation_type=(ProviderOperationType.CREATE_CUSTOMER),
            fields={},
        )
        customer_operation = self._provider_operation_repository.get_by_idempotency_key_for_update(
            session,
            tenant_id=command.tenant_id,
            operation_type=(ProviderOperationType.CREATE_CUSTOMER),
            idempotency_key=command.idempotency_key,
        )

        if customer_operation is not None:
            self._require_matching_fingerprint(
                operation=customer_operation,
                request_fingerprint=customer_fingerprint,
            )
            self._raise_if_operation_cannot_resume(customer_operation)

            if customer_operation.status is ProviderOperationStatus.SUCCEEDED:
                if billing_customer is None or billing_customer.provider_customer_id is None:
                    raise RuntimeError(
                        "Succeeded customer operation has no persisted provider customer."
                    )

                self._claim_operation(
                    subscription_operation,
                    now=now,
                )
                subscription_operation.billing_customer_id = billing_customer.id
                self._provider_operation_repository.flush(
                    session,
                )

                return _ReservedSubscriptionCreation(
                    subscription_operation_id=(subscription_operation.id),
                    customer_operation_id=None,
                    provider_customer_id=(billing_customer.provider_customer_id),
                    replayed_result=None,
                )
        else:
            customer_operation = ProviderOperation(
                id=uuid4(),
                tenant_id=command.tenant_id,
                billing_customer_id=(billing_customer.id if billing_customer is not None else None),
                subscription_id=None,
                provider=self._payment_provider.provider,
                operation_type=(ProviderOperationType.CREATE_CUSTOMER),
                idempotency_key=command.idempotency_key,
                request_fingerprint=customer_fingerprint,
                request_payload={},
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
                customer_operation,
            )

        self._claim_operation(
            customer_operation,
            now=now,
        )
        self._provider_operation_repository.flush(
            session,
        )

        return _ReservedSubscriptionCreation(
            subscription_operation_id=(subscription_operation.id),
            customer_operation_id=customer_operation.id,
            provider_customer_id=None,
            replayed_result=None,
        )

    def _execute_customer_creation(
        self,
        *,
        session: Session,
        command: CreateBillingSubscriptionCommand,
        customer_operation_id: UUID,
        subscription_operation_id: UUID,
    ) -> str:
        self._require_no_open_transaction(session)

        try:
            result = self._payment_provider.create_customer(
                CreateCustomerRequest(
                    provider_operation_key=(build_provider_operation_key(customer_operation_id)),
                )
            )
        except ProviderRetryableError as error:
            self._persist_provider_failure(
                session=session,
                operation_id=customer_operation_id,
                error=error,
            )
            raise
        except ProviderTerminalError as error:
            self._persist_provider_failure(
                session=session,
                operation_id=customer_operation_id,
                error=error,
            )
            raise

        try:
            provider_customer_id = self._apply_customer_result_and_claim_subscription(
                session=session,
                command=command,
                customer_operation_id=(customer_operation_id),
                subscription_operation_id=(subscription_operation_id),
                result=result,
            )
            session.commit()
        except BillingCustomerAlreadyExistsError as error:
            self._rollback_if_active(session)
            self._persist_application_failure(
                session=session,
                operation_id=customer_operation_id,
                failure_code=error.code,
                failure_message=error.public_message,
            )
            raise
        except Exception:
            self._rollback_if_active(session)
            raise

        self._require_no_open_transaction(session)
        return provider_customer_id

    def _apply_customer_result_and_claim_subscription(
        self,
        *,
        session: Session,
        command: CreateBillingSubscriptionCommand,
        customer_operation_id: UUID,
        subscription_operation_id: UUID,
        result: CreateCustomerResult,
    ) -> str:
        now = self._clock.now()
        # Keep the same provider-operation lock order as reservation:
        # subscription operation, then customer operation.
        subscription_operation = self._require_operation_for_update(
            session,
            subscription_operation_id,
        )
        customer_operation = self._require_operation_for_update(
            session,
            customer_operation_id,
        )
        self._require_in_progress(customer_operation)

        billing_customer = self._billing_customer_repository.get_by_tenant_and_provider_for_update(
            session,
            tenant_id=command.tenant_id,
            provider=self._payment_provider.provider,
        )

        if billing_customer is None:
            billing_customer = BillingCustomer(
                id=uuid4(),
                tenant_id=command.tenant_id,
                provider=self._payment_provider.provider,
                provider_customer_id=(result.provider_customer_id),
            )
            self._billing_customer_repository.add_and_flush(
                session,
                billing_customer,
            )
        elif billing_customer.provider_customer_id is None:
            billing_customer.provider_customer_id = result.provider_customer_id
            session.flush()
        elif billing_customer.provider_customer_id != result.provider_customer_id:
            raise BillingCustomerAlreadyExistsError()

        customer_operation.billing_customer_id = billing_customer.id
        self._mark_operation_succeeded(
            operation=customer_operation,
            provider_reference=result.provider_reference,
            result_payload={
                "provider_customer_id": (result.provider_customer_id),
                "provider_reference": (result.provider_reference),
            },
            completed_at=now,
        )

        subscription_operation.billing_customer_id = billing_customer.id
        self._claim_operation(
            subscription_operation,
            now=now,
        )
        self._provider_operation_repository.flush(
            session,
        )

        return result.provider_customer_id

    def _execute_subscription_creation(
        self,
        *,
        session: Session,
        command: CreateBillingSubscriptionCommand,
        subscription_operation_id: UUID,
        provider_customer_id: str,
    ) -> CreatedBillingSubscription:
        self._require_no_open_transaction(session)

        try:
            result = self._payment_provider.create_subscription(
                CreateSubscriptionRequest(
                    provider_operation_key=(
                        build_provider_operation_key(subscription_operation_id)
                    ),
                    provider_customer_id=provider_customer_id,
                    price_code=command.price_code,
                    effective_at=self._clock.now(),
                )
            )
        except ProviderRetryableError as error:
            self._persist_provider_failure(
                session=session,
                operation_id=subscription_operation_id,
                error=error,
            )
            raise
        except ProviderTerminalError as error:
            self._persist_provider_failure(
                session=session,
                operation_id=subscription_operation_id,
                error=error,
            )
            raise

        try:
            return self._apply_subscription_result(
                session=session,
                command=command,
                subscription_operation_id=(subscription_operation_id),
                result=result,
            )
        except BillingSubscriptionAlreadyExistsError as error:
            self._rollback_if_active(session)
            self._persist_application_failure(
                session=session,
                operation_id=subscription_operation_id,
                failure_code=error.code,
                failure_message=error.public_message,
            )
            raise
        except Exception:
            self._rollback_if_active(session)
            raise

    def _apply_subscription_result(
        self,
        *,
        session: Session,
        command: CreateBillingSubscriptionCommand,
        subscription_operation_id: UUID,
        result: CreateSubscriptionResult,
    ) -> CreatedBillingSubscription:
        now = self._clock.now()
        operation = self._require_operation_for_update(
            session,
            subscription_operation_id,
        )
        self._require_in_progress(operation)

        existing_subscription = self._subscription_repository.get_by_tenant_id_for_update(
            session,
            tenant_id=command.tenant_id,
        )
        if existing_subscription is not None:
            raise BillingSubscriptionAlreadyExistsError()

        billing_customer_id = _require_uuid(
            operation.billing_customer_id,
            field_name="billing_customer_id",
        )
        price = get_price_definition(command.price_code)
        subscription = Subscription(
            id=uuid4(),
            tenant_id=command.tenant_id,
            billing_customer_id=billing_customer_id,
            provider=self._payment_provider.provider,
            provider_subscription_id=(result.provider_subscription_id),
            price_code=price.price_code,
            plan=price.plan,
            billing_interval=price.billing_interval,
            currency=price.currency,
            unit_amount=price.unit_amount,
            pending_price_code=None,
            status=SubscriptionStatus.ACTIVE,
            cancel_at_period_end=False,
            cancellation_requested_at=None,
            current_period_start=(result.current_period_start),
            current_period_end=result.current_period_end,
            provider_state_version=(result.provider_state_version),
            last_provider_event_at=None,
            canceled_at=None,
        )
        self._subscription_repository.add_and_flush(
            session,
            subscription,
        )

        operation.subscription_id = subscription.id
        self._mark_operation_succeeded(
            operation=operation,
            provider_reference=result.provider_reference,
            result_payload={
                "provider_subscription_id": (result.provider_subscription_id),
                "provider_state_version": (result.provider_state_version),
                "current_period_start": (result.current_period_start.astimezone(UTC).isoformat()),
                "current_period_end": (result.current_period_end.astimezone(UTC).isoformat()),
                "provider_reference": (result.provider_reference),
            },
            completed_at=now,
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
                action=AuditAction.BILLING_SUBSCRIPTION_CREATED.value,
                resource_type=AuditResourceType.SUBSCRIPTION.value,
                resource_id=str(subscription.id),
                correlation_id=audit_context.correlation_id,
                metadata_version=1,
                metadata={
                    "plan": subscription.plan.value,
                    "status": subscription.status.value,
                },
                idempotency_key=(f"subscription-created:{subscription.id}"),
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

        operation.status = (
            ProviderOperationStatus.FAILED_RETRYABLE
            if error.retryable
            else ProviderOperationStatus.FAILED_TERMINAL
        )
        operation.completed_at = None if error.retryable else self._clock.now()
        operation.failure_code = error.code
        operation.failure_message = error.public_message[:512]
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
        operation.status = ProviderOperationStatus.FAILED_TERMINAL
        operation.completed_at = self._clock.now()
        operation.failure_code = failure_code
        operation.failure_message = failure_message[:512]
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

    def _require_matching_fingerprint(
        self,
        *,
        operation: ProviderOperation,
        request_fingerprint: str,
    ) -> None:
        if operation.request_fingerprint != request_fingerprint:
            raise BillingIdempotencyConflictError()

    def _raise_if_operation_cannot_resume(
        self,
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

    def _claim_operation(
        self,
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

    def _mark_operation_succeeded(
        self,
        *,
        operation: ProviderOperation,
        provider_reference: str,
        result_payload: dict[str, object],
        completed_at: datetime,
    ) -> None:
        self._require_in_progress(operation)
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
    ) -> CreatedBillingSubscription:
        return CreatedBillingSubscription(
            id=subscription.id,
            tenant_id=subscription.tenant_id,
            price_code=subscription.price_code,
            plan=subscription.plan,
            billing_interval=(subscription.billing_interval),
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


def _normalize_optional_datetime(
    value: datetime | None,
    *,
    field_name: str,
) -> datetime | None:
    if value is None:
        return None

    return _normalize_datetime(
        value,
        field_name=field_name,
    )


def _normalize_datetime(
    value: datetime,
    *,
    field_name: str,
) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")

    return value.astimezone(UTC)


def _require_datetime(
    value: datetime | None,
    *,
    field_name: str,
) -> datetime:
    if value is None:
        raise RuntimeError(f"{field_name} is required for an active subscription.")

    return value


def _require_uuid(
    value: UUID | None,
    *,
    field_name: str,
) -> UUID:
    if value is None:
        raise RuntimeError(f"{field_name} is required.")

    return value


def _require_string(
    value: str | None,
    *,
    field_name: str,
) -> str:
    if value is None or not value.strip():
        raise RuntimeError(f"{field_name} is required.")

    return value
