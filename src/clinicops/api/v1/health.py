from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    """Health endpoint response."""

    status: Literal["ok"] = "ok"


@router.get("/live", response_model=HealthResponse, summary="Check process liveness")
def get_liveness() -> HealthResponse:
    """Confirm that the HTTP process is running."""

    return HealthResponse()
