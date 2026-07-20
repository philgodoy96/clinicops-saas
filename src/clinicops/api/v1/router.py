from fastapi import APIRouter

from clinicops.api.v1.health import router as health_router

router = APIRouter()
router.include_router(
    health_router,
    prefix="/health",
    tags=["system"],
)
