from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from threading import Lock
from typing import TypeVar

from clinicops.billing.catalog import (
    PriceDefinition,
    get_price_definition,
)
from clinicops.billing.enums import (
    BillingProvider,
    ProviderOperationType,
)
from clinicops.billing.exceptions import (
    UnsupportedPriceCodeError,
)
from clinicops.billing.providers.contracts import (
    CancelSubscriptionRequest,
    CancelSubscriptionResult,
    ChangePlanRequest,
    ChangePlanResult,
    CreateCustomerRequest,
    CreateCustomerResult,
    CreateSubscriptionRequest,
    CreateSubscriptionResult,
)
from clinicops.billing.providers.control import (
    FakeProviderControl,
    FakeProviderOutcome,
)
from clinicops.billing.providers.exceptions import (
    ProviderAmbiguousOutcomeError,
    ProviderIdempotencyConflictError,
    ProviderInvalidStateError,
    ProviderResourceNotFoundError,
    ProviderRetryableError,
    ProviderTerminalError,
)
from clinicops.billing.providers.idempotency import (
    fingerprint_provider_request,
)
from clinicops.billing.providers.periods import (
    calculate_billing_period,
)

type ProviderResult = (
    CreateCustomerResult | CreateSubscriptionResult | ChangePlanResult | CancelSubscriptionResult
)
ResultT = TypeVar(
    "ResultT",
    CreateCustomerResult,
    CreateSubscriptionResult,
    ChangePlanResult,
    CancelSubscriptionResult,
)


@dataclass(frozen=True, slots=True)
class _StoredOperation:
    operation_type: ProviderOperationType
    request_fingerprint: str
    result: ProviderResult | None = None
    terminal_failure_message: str | None = None

    def __post_init__(self) -> None:
        has_result = self.result is not None
        has_failure = self.terminal_failure_message is not None

        if has_result == has_failure:
            raise ValueError("Stored provider operation must contain exactly one outcome.")


@dataclass(slots=True)
class _FakeSubscription:
    provider_customer_id: str
    price_code: str
    current_period_start: datetime
    current_period_end: datetime
    provider_state_version: int
    pending_price_code: str | None = None
    pending_effective_at: datetime | None = None
    canceled_at: datetime | None = None


class FakePaymentProvider:
    """Deterministic in-memory payment provider for local development."""

    def __init__(
        self,
        *,
        control: FakeProviderControl | None = None,
    ) -> None:
        self._control = control or FakeProviderControl()
        self._lock = Lock()
        self._customers: set[str] = set()
        self._subscriptions: dict[
            str,
            _FakeSubscription,
        ] = {}
        self._subscription_by_customer: dict[str, str] = {}
        self._operations: dict[str, _StoredOperation] = {}

    @property
    def provider(self) -> BillingProvider:
        return BillingProvider.FAKE

    def create_customer(
        self,
        request: CreateCustomerRequest,
    ) -> CreateCustomerResult:
        operation_type = ProviderOperationType.CREATE_CUSTOMER
        request_fingerprint = fingerprint_provider_request(
            operation_type=operation_type,
            fields={},
        )

        with self._lock:
            replayed = self._replay_operation(
                provider_operation_key=(request.provider_operation_key),
                operation_type=operation_type,
                request_fingerprint=request_fingerprint,
            )
            if replayed is not None:
                return self._require_result_type(
                    replayed,
                    CreateCustomerResult,
                )

            outcome = self._control.consume_next(operation_type=operation_type)
            self._raise_pre_mutation_failure(
                provider_operation_key=(request.provider_operation_key),
                operation_type=operation_type,
                request_fingerprint=request_fingerprint,
                outcome=outcome,
            )

            stable_token = _stable_token(request.provider_operation_key)
            result = CreateCustomerResult(
                provider_customer_id=(f"fake_cus_{stable_token}"),
                provider_reference=(f"fake_op_{stable_token}"),
            )

            self._customers.add(result.provider_customer_id)
            self._store_operation_result(
                provider_operation_key=(request.provider_operation_key),
                operation_type=operation_type,
                request_fingerprint=request_fingerprint,
                result=result,
            )

        self._raise_ambiguous_outcome(
            operation_type=operation_type,
            outcome=outcome,
        )
        return result

    def create_subscription(
        self,
        request: CreateSubscriptionRequest,
    ) -> CreateSubscriptionResult:
        operation_type = ProviderOperationType.CREATE_SUBSCRIPTION
        request_fingerprint = fingerprint_provider_request(
            operation_type=operation_type,
            fields={
                "provider_customer_id": (request.provider_customer_id),
                "price_code": request.price_code,
                "effective_at": request.effective_at,
            },
        )

        with self._lock:
            replayed = self._replay_operation(
                provider_operation_key=(request.provider_operation_key),
                operation_type=operation_type,
                request_fingerprint=request_fingerprint,
            )
            if replayed is not None:
                return self._require_result_type(
                    replayed,
                    CreateSubscriptionResult,
                )

            outcome = self._control.consume_next(operation_type=operation_type)
            self._raise_pre_mutation_failure(
                provider_operation_key=(request.provider_operation_key),
                operation_type=operation_type,
                request_fingerprint=request_fingerprint,
                outcome=outcome,
            )
            self._require_customer(
                provider_customer_id=(request.provider_customer_id),
                operation_type=operation_type,
            )
            price = self._resolve_price(
                price_code=request.price_code,
                operation_type=operation_type,
            )

            if request.provider_customer_id in self._subscription_by_customer:
                raise ProviderInvalidStateError(
                    provider=self.provider,
                    operation_type=operation_type,
                    reason=("customer_already_has_subscription"),
                )

            period = calculate_billing_period(
                effective_at=request.effective_at,
                billing_interval=price.billing_interval,
            )
            stable_token = _stable_token(request.provider_operation_key)
            provider_subscription_id = f"fake_sub_{stable_token}"
            result = CreateSubscriptionResult(
                provider_subscription_id=(provider_subscription_id),
                provider_state_version=1,
                current_period_start=period.start,
                current_period_end=period.end,
                provider_reference=(f"fake_op_{stable_token}"),
            )

            self._subscriptions[provider_subscription_id] = _FakeSubscription(
                provider_customer_id=(request.provider_customer_id),
                price_code=price.price_code,
                current_period_start=period.start,
                current_period_end=period.end,
                provider_state_version=1,
            )
            self._subscription_by_customer[request.provider_customer_id] = provider_subscription_id
            self._store_operation_result(
                provider_operation_key=(request.provider_operation_key),
                operation_type=operation_type,
                request_fingerprint=request_fingerprint,
                result=result,
            )

        self._raise_ambiguous_outcome(
            operation_type=operation_type,
            outcome=outcome,
        )
        return result

    def change_plan(
        self,
        request: ChangePlanRequest,
    ) -> ChangePlanResult:
        """Schedule a provider-side price change at period end."""

        operation_type = ProviderOperationType.CHANGE_PLAN
        request_fingerprint = fingerprint_provider_request(
            operation_type=operation_type,
            fields={
                "provider_subscription_id": (request.provider_subscription_id),
                "target_price_code": (request.target_price_code),
                "effective_at": request.effective_at,
            },
        )

        with self._lock:
            replayed = self._replay_operation(
                provider_operation_key=(request.provider_operation_key),
                operation_type=operation_type,
                request_fingerprint=request_fingerprint,
            )
            if replayed is not None:
                return self._require_result_type(
                    replayed,
                    ChangePlanResult,
                )

            outcome = self._control.consume_next(operation_type=operation_type)
            self._raise_pre_mutation_failure(
                provider_operation_key=(request.provider_operation_key),
                operation_type=operation_type,
                request_fingerprint=request_fingerprint,
                outcome=outcome,
            )
            subscription = self._require_subscription(
                provider_subscription_id=(request.provider_subscription_id),
                operation_type=operation_type,
            )
            self._require_mutable_subscription(
                subscription=subscription,
                operation_type=operation_type,
            )
            target_price = self._resolve_price(
                price_code=request.target_price_code,
                operation_type=operation_type,
            )

            if target_price.price_code == subscription.price_code:
                raise ProviderInvalidStateError(
                    provider=self.provider,
                    operation_type=operation_type,
                    reason="price_already_selected",
                )

            if subscription.pending_price_code is not None:
                raise ProviderInvalidStateError(
                    provider=self.provider,
                    operation_type=operation_type,
                    reason="plan_change_already_pending",
                )

            self._require_period_boundary(
                effective_at=request.effective_at,
                current_period_end=(subscription.current_period_end),
                operation_type=operation_type,
            )

            future_period = calculate_billing_period(
                effective_at=request.effective_at,
                billing_interval=(target_price.billing_interval),
            )
            next_version = subscription.provider_state_version + 1
            stable_token = _stable_token(request.provider_operation_key)
            result = ChangePlanResult(
                provider_subscription_id=(request.provider_subscription_id),
                provider_state_version=next_version,
                effective_price_code=(target_price.price_code),
                current_period_start=future_period.start,
                current_period_end=future_period.end,
                provider_reference=(f"fake_op_{stable_token}"),
            )

            subscription.pending_price_code = target_price.price_code
            subscription.pending_effective_at = request.effective_at
            subscription.provider_state_version = next_version
            self._store_operation_result(
                provider_operation_key=(request.provider_operation_key),
                operation_type=operation_type,
                request_fingerprint=request_fingerprint,
                result=result,
            )

        self._raise_ambiguous_outcome(
            operation_type=operation_type,
            outcome=outcome,
        )
        return result

    def cancel_subscription(
        self,
        request: CancelSubscriptionRequest,
    ) -> CancelSubscriptionResult:
        operation_type = ProviderOperationType.CANCEL_SUBSCRIPTION
        request_fingerprint = fingerprint_provider_request(
            operation_type=operation_type,
            fields={
                "provider_subscription_id": (request.provider_subscription_id),
                "effective_at": request.effective_at,
            },
        )

        with self._lock:
            replayed = self._replay_operation(
                provider_operation_key=(request.provider_operation_key),
                operation_type=operation_type,
                request_fingerprint=request_fingerprint,
            )
            if replayed is not None:
                return self._require_result_type(
                    replayed,
                    CancelSubscriptionResult,
                )

            outcome = self._control.consume_next(operation_type=operation_type)
            self._raise_pre_mutation_failure(
                provider_operation_key=(request.provider_operation_key),
                operation_type=operation_type,
                request_fingerprint=request_fingerprint,
                outcome=outcome,
            )
            subscription = self._require_subscription(
                provider_subscription_id=(request.provider_subscription_id),
                operation_type=operation_type,
            )
            self._require_mutable_subscription(
                subscription=subscription,
                operation_type=operation_type,
            )
            self._require_period_boundary(
                effective_at=request.effective_at,
                current_period_end=(subscription.current_period_end),
                operation_type=operation_type,
            )

            next_version = subscription.provider_state_version + 1
            stable_token = _stable_token(request.provider_operation_key)
            result = CancelSubscriptionResult(
                provider_subscription_id=(request.provider_subscription_id),
                provider_state_version=next_version,
                canceled_at=request.effective_at,
                provider_reference=(f"fake_op_{stable_token}"),
            )

            subscription.canceled_at = request.effective_at
            subscription.pending_price_code = None
            subscription.pending_effective_at = None
            subscription.provider_state_version = next_version
            self._store_operation_result(
                provider_operation_key=(request.provider_operation_key),
                operation_type=operation_type,
                request_fingerprint=request_fingerprint,
                result=result,
            )

        self._raise_ambiguous_outcome(
            operation_type=operation_type,
            outcome=outcome,
        )
        return result

    def _replay_operation(
        self,
        *,
        provider_operation_key: str,
        operation_type: ProviderOperationType,
        request_fingerprint: str,
    ) -> ProviderResult | None:
        stored = self._operations.get(provider_operation_key)

        if stored is None:
            return None

        if (
            stored.operation_type is not operation_type
            or stored.request_fingerprint != request_fingerprint
        ):
            raise ProviderIdempotencyConflictError(
                provider=self.provider,
                operation_type=operation_type,
                provider_operation_key=(provider_operation_key),
            )

        if stored.terminal_failure_message is not None:
            raise ProviderTerminalError(
                provider=self.provider,
                operation_type=operation_type,
                internal_message=(stored.terminal_failure_message),
            )

        if stored.result is None:
            raise RuntimeError("Stored provider operation has no replayable outcome.")

        return stored.result

    def _raise_pre_mutation_failure(
        self,
        *,
        provider_operation_key: str,
        operation_type: ProviderOperationType,
        request_fingerprint: str,
        outcome: FakeProviderOutcome,
    ) -> None:
        if outcome is FakeProviderOutcome.RETRYABLE_FAILURE:
            raise ProviderRetryableError(
                provider=self.provider,
                operation_type=operation_type,
                internal_message=("Fake provider simulated a temporary failure."),
            )

        if outcome is FakeProviderOutcome.TERMINAL_REJECTION:
            failure_message = "Fake provider simulated a terminal rejection."
            self._operations[provider_operation_key] = _StoredOperation(
                operation_type=operation_type,
                request_fingerprint=request_fingerprint,
                terminal_failure_message=failure_message,
            )
            raise ProviderTerminalError(
                provider=self.provider,
                operation_type=operation_type,
                internal_message=failure_message,
            )

    def _raise_ambiguous_outcome(
        self,
        *,
        operation_type: ProviderOperationType,
        outcome: FakeProviderOutcome,
    ) -> None:
        if outcome is FakeProviderOutcome.AMBIGUOUS_SUCCESS:
            raise ProviderAmbiguousOutcomeError(
                provider=self.provider,
                operation_type=operation_type,
                internal_message=(
                    "Fake provider applied the mutation but "
                    "simulated a timeout before confirmation."
                ),
            )

    def _store_operation_result(
        self,
        *,
        provider_operation_key: str,
        operation_type: ProviderOperationType,
        request_fingerprint: str,
        result: ProviderResult,
    ) -> None:
        self._operations[provider_operation_key] = _StoredOperation(
            operation_type=operation_type,
            request_fingerprint=(request_fingerprint),
            result=result,
        )

    def _require_customer(
        self,
        *,
        provider_customer_id: str,
        operation_type: ProviderOperationType,
    ) -> None:
        if provider_customer_id not in self._customers:
            raise ProviderResourceNotFoundError(
                provider=self.provider,
                operation_type=operation_type,
                resource_type="customer",
                resource_id=provider_customer_id,
            )

    def _require_subscription(
        self,
        *,
        provider_subscription_id: str,
        operation_type: ProviderOperationType,
    ) -> _FakeSubscription:
        subscription = self._subscriptions.get(provider_subscription_id)

        if subscription is None:
            raise ProviderResourceNotFoundError(
                provider=self.provider,
                operation_type=operation_type,
                resource_type="subscription",
                resource_id=provider_subscription_id,
            )

        return subscription

    def _resolve_price(
        self,
        *,
        price_code: str,
        operation_type: ProviderOperationType,
    ) -> PriceDefinition:
        try:
            return get_price_definition(price_code)
        except UnsupportedPriceCodeError as error:
            raise ProviderInvalidStateError(
                provider=self.provider,
                operation_type=operation_type,
                reason="unsupported_price_code",
            ) from error

    def _require_mutable_subscription(
        self,
        *,
        subscription: _FakeSubscription,
        operation_type: ProviderOperationType,
    ) -> None:
        if subscription.canceled_at is not None:
            raise ProviderInvalidStateError(
                provider=self.provider,
                operation_type=operation_type,
                reason="subscription_already_canceled",
            )

    def _require_period_boundary(
        self,
        *,
        effective_at: datetime,
        current_period_end: datetime,
        operation_type: ProviderOperationType,
    ) -> None:
        if effective_at < current_period_end:
            raise ProviderInvalidStateError(
                provider=self.provider,
                operation_type=operation_type,
                reason=("billing_period_boundary_not_reached"),
            )

    @staticmethod
    def _require_result_type(
        result: ProviderResult,
        expected_type: type[ResultT],
    ) -> ResultT:
        if not isinstance(result, expected_type):
            raise RuntimeError(
                "Stored provider operation result type does not match its operation type."
            )

        return result


def _stable_token(
    provider_operation_key: str,
) -> str:
    return sha256(provider_operation_key.encode("utf-8")).hexdigest()[:24]
