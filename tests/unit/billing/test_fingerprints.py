from clinicops.billing.enums import (
    BillingInterval,
    ProviderOperationType,
)
from clinicops.billing.fingerprints import (
    FingerprintValue,
    fingerprint_billing_command,
)


def test_provider_operation_types_expose_stable_values() -> None:
    assert ProviderOperationType.CREATE_CUSTOMER.value == "create_customer"
    assert ProviderOperationType.CREATE_SUBSCRIPTION.value == "create_subscription"
    assert ProviderOperationType.CHANGE_PLAN.value == "change_plan"
    assert ProviderOperationType.CANCEL_SUBSCRIPTION.value == "cancel_subscription"


def test_fingerprint_matches_the_canonical_contract() -> None:
    fingerprint = fingerprint_billing_command(
        operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
        fields={
            "price_code": "starter_monthly",
        },
    )

    assert fingerprint == ("7fa968d6085fec8e4d314a3210c13a8d2321e853001a0fe5aece3d6458f0c8e7")


def test_fingerprint_is_independent_of_field_insertion_order() -> None:
    first = fingerprint_billing_command(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        fields={
            "price_code": "professional_yearly",
            "apply_at_period_end": True,
        },
    )

    second = fingerprint_billing_command(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        fields={
            "apply_at_period_end": True,
            "price_code": "professional_yearly",
        },
    )

    assert first == second


def test_fingerprint_changes_with_the_operation_type() -> None:
    fields = {
        "price_code": "professional_monthly",
    }

    create_fingerprint = fingerprint_billing_command(
        operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
        fields=fields,
    )

    change_fingerprint = fingerprint_billing_command(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        fields=fields,
    )

    assert create_fingerprint != change_fingerprint


def test_fingerprint_changes_with_command_fields() -> None:
    starter_fingerprint = fingerprint_billing_command(
        operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
        fields={
            "price_code": "starter_monthly",
        },
    )

    professional_fingerprint = fingerprint_billing_command(
        operation_type=(ProviderOperationType.CREATE_SUBSCRIPTION),
        fields={
            "price_code": "professional_monthly",
        },
    )

    assert starter_fingerprint != professional_fingerprint


def test_fingerprint_serializes_enum_values_explicitly() -> None:
    enum_fingerprint = fingerprint_billing_command(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        fields={
            "billing_interval": BillingInterval.YEARLY,
        },
    )

    string_fingerprint = fingerprint_billing_command(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        fields={
            "billing_interval": "yearly",
        },
    )

    assert enum_fingerprint == string_fingerprint


def test_fingerprint_supports_a_command_without_body_fields() -> None:
    first = fingerprint_billing_command(
        operation_type=(ProviderOperationType.CANCEL_SUBSCRIPTION),
        fields={},
    )

    second = fingerprint_billing_command(
        operation_type=(ProviderOperationType.CANCEL_SUBSCRIPTION),
        fields={},
    )

    assert first == second
    assert len(first) == 64


def test_fingerprint_does_not_modify_input_fields() -> None:
    fields: dict[str, FingerprintValue] = {
        "price_code": "starter_yearly",
        "apply_at_period_end": True,
    }

    original_fields = dict(fields)

    fingerprint_billing_command(
        operation_type=ProviderOperationType.CHANGE_PLAN,
        fields=fields,
    )

    assert fields == original_fields
