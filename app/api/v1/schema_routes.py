"""
Versioned schema routes for /api/v1/schema and /api/v1/schema/{table_name}.

Uses SchemaService as the application layer (not SchemaManager directly).
Errors are returned as ErrorResponse (error_code, safe message, request_id).
Internal exception details are logged but never sent to the client.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.models.response_models import ErrorResponse
from app.models.schema_models import DatabaseSchema, TableSchema
from app.services.schema_service import SchemaService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Schema v1"])


def _request_id(request: Request) -> str | None:
    """Extract the request ID from request state (set by RequestIDMiddleware)."""
    return getattr(request.state, "request_id", None)


@router.get(
    "/schema",
    response_model=DatabaseSchema,
    summary="Full database schema (v1)",
    responses={
        500: {"model": ErrorResponse, "description": "Database inspection failed"},
    },
)
def get_full_schema(request: Request) -> DatabaseSchema:
    """Return the complete schema of the connected database.

    The response includes all tables with their columns, primary keys,
    foreign keys, and unique constraints.

    Returns:
        200 DatabaseSchema on success.
        500 ErrorResponse (detail) with error_code='SCHEMA_RETRIEVAL_FAILED'
            on database errors.
    """
    rid = _request_id(request)
    try:
        service = SchemaService()
        return service.get_full_schema()
    except SQLAlchemyError as exc:
        logger.exception(
            "Schema retrieval failed [request_id=%s]: %s", rid, exc
        )
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error_code="SCHEMA_RETRIEVAL_FAILED",
                message="Failed to retrieve database schema. Ensure the database is running.",
                request_id=rid,
            ).model_dump(),
        )


@router.get(
    "/schema/{table_name}",
    response_model=TableSchema,
    summary="Single table schema (v1)",
    responses={
        404: {"model": ErrorResponse, "description": "Table not found"},
        500: {"model": ErrorResponse, "description": "Database inspection failed"},
    },
)
def get_table_schema(table_name: str, request: Request) -> TableSchema:
    """Return schema information for a specific table.

    Returns:
        200 TableSchema on success.
        404 ErrorResponse (detail) with error_code='TABLE_NOT_FOUND'
            when the table does not exist.
        500 ErrorResponse (detail) with error_code='SCHEMA_RETRIEVAL_FAILED'
            on database errors.
    """
    rid = _request_id(request)
    try:
        service = SchemaService()
        return service.get_table_schema(table_name)
    except ValueError:
        # ValueError means the table does not exist — no need to log at ERROR level
        logger.info(
            "Table '%s' not found in database [request_id=%s]", table_name, rid
        )
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                error_code="TABLE_NOT_FOUND",
                message=f"Table '{table_name}' does not exist in the connected database.",
                request_id=rid,
            ).model_dump(),
        )
    except SQLAlchemyError as exc:
        logger.exception(
            "Schema retrieval failed for table '%s' [request_id=%s]: %s",
            table_name,
            rid,
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error_code="SCHEMA_RETRIEVAL_FAILED",
                message=f"Failed to retrieve schema for table '{table_name}'.",
                request_id=rid,
            ).model_dump(),
        )
