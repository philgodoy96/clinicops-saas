from enum import StrEnum


class AuditAction(StrEnum):
    TENANT_CREATED = "tenant.created"
    TENANT_OWNERSHIP_TRANSFERRED = "tenant.ownership_transferred"

    MEMBERSHIP_ROLE_CHANGED = "membership.role_changed"
    MEMBERSHIP_REMOVED = "membership.removed"

    INVITATION_CREATED = "invitation.created"
    INVITATION_ACCEPTED = "invitation.accepted"
    INVITATION_REVOKED = "invitation.revoked"

    BILLING_SUBSCRIPTION_CREATED = "billing.subscription.created"
    BILLING_SUBSCRIPTION_PLAN_CHANGED = "billing.subscription.plan_changed"
    BILLING_SUBSCRIPTION_CANCELLED = "billing.subscription.cancelled"

    BILLING_WEBHOOK_PROCESSED = "billing.webhook.processed"
    BILLING_WEBHOOK_IGNORED = "billing.webhook.ignored"

    PATIENT_CREATED = "patient.created"
    PATIENT_UPDATED = "patient.updated"
    PATIENT_ARCHIVED = "patient.archived"
    PATIENT_RESTORED = "patient.restored"

    PROFESSIONAL_CREATED = "professional.created"
    PROFESSIONAL_UPDATED = "professional.updated"
    PROFESSIONAL_ARCHIVED = "professional.archived"
    PROFESSIONAL_RESTORED = "professional.restored"
    PROFESSIONAL_MEMBERSHIP_LINKED = "professional.membership_linked"
    PROFESSIONAL_MEMBERSHIP_UNLINKED = "professional.membership_unlinked"


class AuditResourceType(StrEnum):
    TENANT = "tenant"
    MEMBERSHIP = "membership"
    INVITATION = "invitation"
    SUBSCRIPTION = "subscription"
    BILLING_WEBHOOK_EVENT = "billing_webhook_event"
    PATIENT = "patient"
    PROFESSIONAL = "professional"


__all__ = [
    "AuditAction",
    "AuditResourceType",
]
