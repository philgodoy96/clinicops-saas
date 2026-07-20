from fastapi import APIRouter

from clinicops.api.v1.router import router as v1_router

API_V1_PREFIX = "/api/v1"

api_router = APIRouter()
api_router.include_router(
    v1_router,
    prefix=API_V1_PREFIX,
)
