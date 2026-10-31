"""
Reusable Pydantic response models for Phase 2 API responses.

These models define the contract for:
  - Health-check responses
  - Consistent error responses (error_code + safe message + request_id)

The schema inspection models (DatabaseSchema, TableSchema, etc.) remain
in schema_models.py and are not duplicated here.
"""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Response model for GET /api/v1/health."""

    status: str = Field(..., description="Application status: 'ok' or 'degraded'")
    database: str = Field(..., description="Database connectivity: 'connected' or 'disconnected'")
    request_id: str | None = Field(None, description="Unique identifier for this request")


class ErrorResponse(BaseModel):
    """Consistent error response returned by all v1 API error paths.

    Internal exception details are NEVER included here — they are logged
    server-side only. Clients receive a machine-readable error_code, a
    safe human-readable message, and the request_id for correlation.
    """

    error_code: str = Field(
        ...,
        description="Machine-readable error code (e.g. TABLE_NOT_FOUND, INTERNAL_ERROR)",
    )
    message: str = Field(
        ...,
        description="Safe, user-facing error description — never contains credentials or stack traces",
    )
    request_id: str | None = Field(
        None,
        description="Request ID for correlating this error with server logs",
    )
