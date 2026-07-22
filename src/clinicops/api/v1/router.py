from fastapi import APIRouter

from clinicops.api.v1.authentication.routes import (
    router as authentication_router,
)
from clinicops.api.v1.billing.routes import (
    router as billing_router,
)
from clinicops.api.v1.health import router as health_router
from clinicops.api.v1.invitations.routes import (
    router as invitation_router,
)
from clinicops.api.v1.tenants.routes import router as tenant_router

router = APIRouter()
router.include_router(
    health_router,
    prefix="/health",
    tags=["system"],
)
router.include_router(
    authentication_router,
    prefix="/auth",
)
router.include_router(invitation_router)
router.include_router(tenant_router)
router.include_router(billing_router)
