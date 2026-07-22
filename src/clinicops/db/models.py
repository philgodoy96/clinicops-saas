from sqlalchemy import MetaData

from clinicops.authentication.models import (
    AuthSession,
    AuthSessionStatus,
    RefreshToken,
    RefreshTokenStatus,
)
from clinicops.billing.models import (
    BillingCustomer,
    BillingWebhookEvent,
    ProviderOperation,
    Subscription,
)
from clinicops.db.base import Base
from clinicops.identity.models import PasswordCredential, User, UserStatus
from clinicops.invitations.models import Invitation, InvitationStatus
from clinicops.jobs.models import BackgroundJob
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)

metadata: MetaData = Base.metadata

__all__ = [
    "AuthSession",
    "AuthSessionStatus",
    "BackgroundJob",
    "BillingCustomer",
    "BillingWebhookEvent",
    "Invitation",
    "InvitationStatus",
    "Membership",
    "MembershipStatus",
    "PasswordCredential",
    "ProviderOperation",
    "RefreshToken",
    "RefreshTokenStatus",
    "Subscription",
    "Tenant",
    "TenantRole",
    "TenantStatus",
    "User",
    "UserStatus",
    "metadata",
]
