class ApplicationError(Exception):
    """Base class for expected application failures."""

    code = "application_error"
    public_message = "The request could not be completed."

    def __init__(self, internal_message: str | None = None) -> None:
        super().__init__(internal_message or self.public_message)
