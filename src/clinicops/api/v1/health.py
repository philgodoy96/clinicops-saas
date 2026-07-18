from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

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
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The database is unavailable.",
        }
    },
    summary="Check application readiness",
)
def get_readiness() -> HealthResponse:
    """Confirm that the application can connect to PostgreSQL."""

    try:
        check_database_connection()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc

    return HealthResponse()
