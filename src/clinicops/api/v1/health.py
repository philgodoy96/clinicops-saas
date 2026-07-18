from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from clinicops.db.session import check_database_connection

router = APIRouter()


class HealthResponse(BaseModel):
    """Health endpoint response."""

    status: Literal["ok"] = "ok"


@router.get("/live", response_model=HealthResponse, summary="Check process liveness")
def get_liveness() -> HealthResponse:
    """Confirm that the HTTP process is running."""

    return HealthResponse()


@router.get(
    "/ready",
    response_model=HealthResponse,
    summary="Check application readiness",
)
def get_readiness() -> HealthResponse:
    """Confirm that the application can connect to PostgreSQL."""

    check_database_connection()
    return HealthResponse()
