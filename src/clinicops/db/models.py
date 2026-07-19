from sqlalchemy import MetaData

from clinicops.db.base import Base
from clinicops.identity.models import PasswordCredential, User, UserStatus

metadata: MetaData = Base.metadata

__all__ = [
    "PasswordCredential",
    "User",
    "UserStatus",
    "metadata",
]
