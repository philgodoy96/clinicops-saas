from clinicops.core.exceptions import ApplicationError


class BillingError(ApplicationError):
    """Base error for billing application and domain failures."""

    code = "billing_error"
    public_message = "The billing operation could not be completed."


class UnsupportedPriceCodeError(BillingError):
    """Raised when a price code does not exist in the billing catalog."""

    code = "unsupported_price_code"
    public_message = "The selected billing price is not supported."

    price_code: str

    def __init__(self, price_code: str) -> None:
        self.price_code = price_code
        super().__init__(f"Unsupported billing price code: {price_code!r}.")
