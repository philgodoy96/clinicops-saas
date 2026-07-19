from clinicops.core.exceptions import ApplicationError


class DatabaseUnavailableError(ApplicationError):
    """Raised when PostgreSQL cannot serve an application operation."""

    code = "database_unavailable"
    public_message = "The database is unavailable."
