from clinicops.audit.actions import (
    AuditAction,
    AuditResourceType,
)


def test_audit_actions_have_stable_values() -> None:
    assert AuditAction.TENANT_CREATED.value == "tenant.created"
    assert AuditAction.TENANT_OWNERSHIP_TRANSFERRED.value == "tenant.ownership_transferred"

    assert AuditAction.MEMBERSHIP_ROLE_CHANGED.value == "membership.role_changed"
    assert AuditAction.MEMBERSHIP_REMOVED.value == "membership.removed"

    assert AuditAction.INVITATION_CREATED.value == "invitation.created"
    assert AuditAction.INVITATION_ACCEPTED.value == "invitation.accepted"
    assert AuditAction.INVITATION_REVOKED.value == "invitation.revoked"

    assert AuditAction.BILLING_SUBSCRIPTION_CREATED.value == "billing.subscription.created"
    assert (
        AuditAction.BILLING_SUBSCRIPTION_PLAN_CHANGED.value == "billing.subscription.plan_changed"
    )
    assert AuditAction.BILLING_SUBSCRIPTION_CANCELLED.value == "billing.subscription.cancelled"

    assert AuditAction.BILLING_WEBHOOK_PROCESSED.value == "billing.webhook.processed"
    assert AuditAction.BILLING_WEBHOOK_IGNORED.value == "billing.webhook.ignored"


def test_audit_resource_types_have_stable_values() -> None:
    assert AuditResourceType.TENANT.value == "tenant"
    assert AuditResourceType.MEMBERSHIP.value == "membership"
    assert AuditResourceType.INVITATION.value == "invitation"
    assert AuditResourceType.SUBSCRIPTION.value == "subscription"
    assert AuditResourceType.BILLING_WEBHOOK_EVENT.value == "billing_webhook_event"


def test_action_values_are_unique() -> None:
    values = [action.value for action in AuditAction]

    assert len(values) == len(set(values))


def test_resource_type_values_are_unique() -> None:
    values = [resource_type.value for resource_type in AuditResourceType]

    assert len(values) == len(set(values))
