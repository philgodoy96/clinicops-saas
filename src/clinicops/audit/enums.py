from enum import StrEnum


class AuditActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"


class AuditSource(StrEnum):
    HTTP = "http"
    WORKER = "worker"
    CLI = "cli"
    SYSTEM = "system"


__all__ = [
    "AuditActorType",
    "AuditSource",
]
