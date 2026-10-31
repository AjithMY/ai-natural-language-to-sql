"""
Versioned health-check route for /api/v1/health.

Returns a HealthResponse model (status, database, request_id).
Errors are returned as ErrorResponse (error_code, safe message, request_id).
Internal exception details are logged but never sent to the client.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from app.database.connection import verify_connection
from app.models.response_models import ErrorResponse, HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health v1"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check (v1)",
    responses={
        503: {"model": ErrorResponse, "description": "Database unreachable"},
    },
)
def health_check(request: Request) -> HealthResponse:
    """Verify the API is running and the database is reachable.

    Returns:
        200 HealthResponse with status='ok' and database='connected'.
        503 ErrorResponse (detail) with error_code='DATABASE_UNAVAILABLE'
            when the database cannot be reached.
    """
    request_id: str | None = getattr(request.state, "request_id", None)

    if verify_connection():
        return HealthResponse(
            status="ok",
            database="connected",
            request_id=request_id,
        )

    logger.warning("Health check: database is unreachable [request_id=%s]", request_id)
    raise HTTPException(
        status_code=503,
        detail=ErrorResponse(
            error_code="DATABASE_UNAVAILABLE",
            message="Database is not reachable. Check your server configuration.",
            request_id=request_id,
        ).model_dump(),
    )
