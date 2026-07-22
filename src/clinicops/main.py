from fastapi import FastAPI

from clinicops.api.errors import register_exception_handlers
from clinicops.api.middleware.request_context import RequestContextMiddleware
from clinicops.api.router import api_router
from clinicops.billing.providers.fake import (
    FakePaymentProvider,
)
from clinicops.core.config import Settings, get_settings
from clinicops.core.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the ClinicOps FastAPI application."""

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    application = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
    )
    application.state.settings = resolved_settings
    application.state.payment_provider = FakePaymentProvider()
    register_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware)
    application.include_router(api_router)
    return application


app = create_app()
