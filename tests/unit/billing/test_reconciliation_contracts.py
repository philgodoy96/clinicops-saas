from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from clinicops.billing.enums import (
    BillingProvider,
    BillingReconciliationOutcome,
    SubscriptionStatus,
)
from clinicops.billing.exceptions import (
    BillingReconciliationConflictError,
    BillingReconciliationInvalidSnapshotError,
    BillingReconciliationProviderIdentityMismatchError,
    BillingReconciliationProviderStateNotFoundError,
    BillingReconciliationSubscriptionNotFoundError,
    BillingReconciliationUnsupportedPriceError,
)
from clinicops.billing.reconciliation import (
    BillingProviderSubscriptionSnapshot,
    ReconcileBillingSubscriptionCommand,
    ReconciledBillingSubscription,
)

PERIOD_START = datetime(
    2026,
    8,
    22,
    12,
    tzinfo=UTC,
)
PERIOD_END = datetime(
    2026,
    9,
    22,
    12,
    tzinfo=UTC,
)
OBSERVED_AT = datetime(
    2026,
    8,
    22,
    12,
    5,
    tzinfo=UTC,
)


def _snapshot(
    *,
    status: SubscriptionStatus = (SubscriptionStatus.ACTIVE),
    cancel_at_period_end: bool = False,
    canceled_at: datetime | None = None,
) -> BillingProviderSubscriptionSnapshot:
    return BillingProviderSubscriptionSnapshot(
        provider=BillingProvider.FAKE,
        provider_subscription_id=" fake_sub_01 ",
        provider_state_version=5,
        price_code=" starter_monthly ",
        status=status,
        current_period_start=PERIOD_START,
        current_period_end=PERIOD_END,
        cancel_at_period_end=cancel_at_period_end,
        canceled_at=canceled_at,
        observed_at=OBSERVED_AT,
    )


def test_reconciliation_command_is_immutable() -> None:
    command = ReconcileBillingSubscriptionCommand(subscription_id=uuid4())

    with pytest.raises(FrozenInstanceError):
        command.subscription_id = uuid4()  # type: ignore[misc]


def test_reconciliation_command_requires_uuid() -> None:
    with pytest.raises(
        TypeError,
        match="must be a UUID",
    ):
        ReconcileBillingSubscriptionCommand(
            subscription_id="not-a-uuid",  # type: ignore[arg-type]
        )


def test_active_snapshot_normalizes_provider_identifiers() -> None:
    snapshot = _snapshot(cancel_at_period_end=True)

    assert snapshot.provider_subscription_id == ("fake_sub_01")
    assert snapshot.price_code == "starter_monthly"
    assert snapshot.status is SubscriptionStatus.ACTIVE
    assert snapshot.cancel_at_period_end is True
    assert snapshot.canceled_at is None


def test_canceled_snapshot_requires_consistent_lifecycle() -> None:
    snapshot = _snapshot(
        status=SubscriptionStatus.CANCELED,
        cancel_at_period_end=False,
        canceled_at=PERIOD_END,
    )

    assert snapshot.status is SubscriptionStatus.CANCELED
    assert snapshot.canceled_at == PERIOD_END


@pytest.mark.parametrize(
    (
        "status",
        "cancel_at_period_end",
        "canceled_at",
        "message",
    ),
    [
        (
            SubscriptionStatus.ACTIVE,
            False,
            PERIOD_END,
            "must not include canceled_at",
        ),
        (
            SubscriptionStatus.CANCELED,
            True,
            PERIOD_END,
            "must not remain scheduled",
        ),
        (
            SubscriptionStatus.CANCELED,
            False,
            None,
            "must include canceled_at",
        ),
    ],
)
def test_snapshot_rejects_inconsistent_lifecycle(
    status: SubscriptionStatus,
    cancel_at_period_end: bool,
    canceled_at: datetime | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _snapshot(
            status=status,
            cancel_at_period_end=(cancel_at_period_end),
            canceled_at=canceled_at,
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "current_period_start",
        "current_period_end",
        "observed_at",
    ],
)
def test_snapshot_requires_timezone_aware_timestamps(
    field_name: str,
) -> None:
    arguments = {
        "provider": BillingProvider.FAKE,
        "provider_subscription_id": "fake_sub_01",
        "provider_state_version": 5,
        "price_code": "starter_monthly",
        "status": SubscriptionStatus.ACTIVE,
        "current_period_start": PERIOD_START,
        "current_period_end": PERIOD_END,
        "cancel_at_period_end": False,
        "canceled_at": None,
        "observed_at": OBSERVED_AT,
    }
    arguments[field_name] = datetime(
        2026,
        8,
        22,
        12,
    )

    with pytest.raises(
        ValueError,
        match="timezone-aware",
    ):
        BillingProviderSubscriptionSnapshot(
            **arguments  # type: ignore[arg-type]
        )


def test_snapshot_rejects_unsupported_status() -> None:
    with pytest.raises(
        ValueError,
        match="not supported",
    ):
        BillingProviderSubscriptionSnapshot(
            provider=BillingProvider.FAKE,
            provider_subscription_id="fake_sub_01",
            provider_state_version=5,
            price_code="starter_monthly",
            status=SubscriptionStatus.PAST_DUE,
            current_period_start=PERIOD_START,
            current_period_end=PERIOD_END,
            cancel_at_period_end=False,
            canceled_at=None,
            observed_at=OBSERVED_AT,
        )


def test_in_sync_result_requires_no_drift_and_equal_version() -> None:
    result = ReconciledBillingSubscription(
        subscription_id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_subscription_id=" fake_sub_01 ",
        outcome=BillingReconciliationOutcome.IN_SYNC,
        drift_fields=(),
        previous_provider_state_version=5,
        provider_state_version=5,
    )

    assert result.provider_subscription_id == ("fake_sub_01")
    assert result.drift_fields == ()


def test_repaired_result_normalizes_drift_fields() -> None:
    result = ReconciledBillingSubscription(
        subscription_id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_subscription_id="fake_sub_01",
        outcome=BillingReconciliationOutcome.REPAIRED,
        drift_fields=(
            " unit_amount ",
            "price_code",
            "plan",
        ),
        previous_provider_state_version=4,
        provider_state_version=5,
    )

    assert result.drift_fields == (
        "plan",
        "price_code",
        "unit_amount",
    )


def test_ignored_result_requires_older_snapshot() -> None:
    result = ReconciledBillingSubscription(
        subscription_id=uuid4(),
        provider=BillingProvider.FAKE,
        provider_subscription_id="fake_sub_01",
        outcome=BillingReconciliationOutcome.IGNORED,
        drift_fields=("provider_state_version",),
        previous_provider_state_version=6,
        provider_state_version=5,
    )

    assert result.outcome is BillingReconciliationOutcome.IGNORED


@pytest.mark.parametrize(
    (
        "outcome",
        "drift_fields",
        "previous_version",
        "provider_version",
    ),
    [
        (
            BillingReconciliationOutcome.IN_SYNC,
            ("status",),
            5,
            5,
        ),
        (
            BillingReconciliationOutcome.IN_SYNC,
            (),
            4,
            5,
        ),
        (
            BillingReconciliationOutcome.REPAIRED,
            (),
            4,
            5,
        ),
        (
            BillingReconciliationOutcome.REPAIRED,
            ("status",),
            5,
            4,
        ),
        (
            BillingReconciliationOutcome.IGNORED,
            (),
            5,
            5,
        ),
    ],
)
def test_reconciliation_result_rejects_inconsistent_outcome(
    outcome: BillingReconciliationOutcome,
    drift_fields: tuple[str, ...],
    previous_version: int,
    provider_version: int,
) -> None:
    with pytest.raises(ValueError):
        ReconciledBillingSubscription(
            subscription_id=uuid4(),
            provider=BillingProvider.FAKE,
            provider_subscription_id="fake_sub_01",
            outcome=outcome,
            drift_fields=drift_fields,
            previous_provider_state_version=(previous_version),
            provider_state_version=provider_version,
        )


def test_reconciliation_result_rejects_duplicate_drift_fields() -> None:
    with pytest.raises(
        ValueError,
        match="must be unique",
    ):
        ReconciledBillingSubscription(
            subscription_id=uuid4(),
            provider=BillingProvider.FAKE,
            provider_subscription_id="fake_sub_01",
            outcome=(BillingReconciliationOutcome.REPAIRED),
            drift_fields=("status", " status "),
            previous_provider_state_version=4,
            provider_state_version=5,
        )


def test_reconciliation_errors_have_stable_public_codes() -> None:
    errors = (
        BillingReconciliationSubscriptionNotFoundError(),
        BillingReconciliationProviderStateNotFoundError(
            provider_subscription_id="fake_sub_missing"
        ),
        BillingReconciliationProviderIdentityMismatchError(),
        BillingReconciliationUnsupportedPriceError(price_code="unknown_price"),
        BillingReconciliationInvalidSnapshotError(internal_message="Malformed provider state."),
        BillingReconciliationConflictError(conflict_code="equal_version_state_conflict"),
    )

    assert [error.code for error in errors] == [
        "billing_reconciliation_subscription_not_found",
        "billing_reconciliation_provider_state_not_found",
        "billing_reconciliation_provider_identity_mismatch",
        "billing_reconciliation_unsupported_price",
        "billing_reconciliation_invalid_snapshot",
        "billing_reconciliation_conflict",
    ]

    assert all(
        "fake_sub_missing" not in error.public_message
        and "unknown_price" not in error.public_message
        and "Malformed provider state" not in error.public_message
        and "equal_version_state_conflict" not in error.public_message
        for error in errors
    )
