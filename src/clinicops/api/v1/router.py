from fastapi import APIRouter

from clinicops.api.v1 import health

router = APIRouter()
router.include_router(health.router, prefix="/health", tags=["health"])
