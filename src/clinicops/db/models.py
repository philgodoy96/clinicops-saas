from sqlalchemy import MetaData

from clinicops.db.base import Base
from clinicops.identity.models import PasswordCredential, User, UserStatus
from clinicops.tenancy.models import (
    Membership,
    MembershipStatus,
    Tenant,
    TenantRole,
    TenantStatus,
)

metadata: MetaData = Base.metadata

__all__ = [
    "Membership",
    "MembershipStatus",
    "PasswordCredential",
    "Tenant",
    "TenantRole",
    "TenantStatus",
    "User",
    "UserStatus",
    "metadata",
]
