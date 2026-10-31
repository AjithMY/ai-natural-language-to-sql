"""
Query execution route for /api/v1/query/execute.

Phase 4B — Safe SQL Execution.

Accepts a SQL query, validates it through Phase 4A, and if validation
passes, executes it read-only against the database with row limits
and timeout protection.

Security model:
  - SQL must pass Phase 4A validation BEFORE execution.
  - Only SELECT statements are executed.
  - Results are capped at SQL_MAX_ROWS (configurable, default 1000).
  - Statement-level timeout via MySQL max_execution_time.
  - Error responses never contain credentials, API keys, or stack traces.
  - The Gemini API key is NEVER included in responses or logs.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.models.query_models import (
    QueryExecutionRequest,
    QueryExecutionResponse,
)
from app.models.response_models import ErrorResponse
from app.services.sql_execution_service import SQLExecutionError, SQLExecutionService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Query Execution v1"])


def _request_id(request: Request) -> str | None:
    """Extract the request ID from request state (set by RequestIDMiddleware)."""
    return getattr(request.state, "request_id", None)


@router.post(
    "/query/execute",
    response_model=QueryExecutionResponse,
    summary="Validate and execute a SQL query (v1)",
    responses={
        422: {"model": ErrorResponse, "description": "SQL failed validation"},
        500: {
            "model": ErrorResponse,
            "description": "Schema retrieval or execution failure",
        },
    },
)
def execute_sql_query(
    body: QueryExecutionRequest,
    request: Request,
) -> QueryExecutionResponse:
    """Validate and execute a SQL SELECT query against the database.

    Workflow:
      1. Validate the SQL using Phase 4A (SQLGlot AST parser + schema check).
      2. If validation fails, return 422 with validation errors — SQL is NOT executed.
      3. If validation passes, execute the query read-only with:
         - Row limit: SQL_MAX_ROWS (default 1000)
         - Timeout: SQL_QUERY_TIMEOUT_SECONDS (default 30s)
      4. Return structured results.

    Request body::

        {"sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;"}

    Successful response (200)::

        {
            "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;",
            "columns": ["name", "marks"],
            "rows": [["Alice", 95], ["Bob", 91]],
            "row_count": 2,
            "truncated": false,
            "max_rows": 1000,
            "validation": {"valid": true, ...},
            "request_id": "..."
        }

    Returns:
        200 QueryExecutionResponse on success.
        422 ErrorResponse with error_code='SQL_VALIDATION_FAILED' on validation failure.
        500 ErrorResponse with error_code='SCHEMA_RETRIEVAL_FAILED' on DB schema errors.
        500 ErrorResponse with error_code='SQL_EXECUTION_FAILED' on execution errors.
    """
    rid = _request_id(request)

    try:
        service = SQLExecutionService()
        result = service.execute(body.sql)

    except SQLAlchemyError as exc:
        logger.exception(
            "Schema retrieval failed during query execution [request_id=%s]: %s",
            rid,
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error_code="SCHEMA_RETRIEVAL_FAILED",
                message="Failed to retrieve database schema for query execution. "
                        "Ensure the database is running.",
                request_id=rid,
            ).model_dump(),
        )

    except SQLExecutionError as exc:
        safe_message = str(exc)
        logger.error(
            "SQL execution failed [request_id=%s]: %s", rid, safe_message
        )
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error_code="SQL_EXECUTION_FAILED",
                message=safe_message,
                request_id=rid,
            ).model_dump(),
        )

    # Check if validation failed (SQL was NOT executed)
    validation = result["validation"]
    if not validation.valid:
        logger.warning(
            "SQL validation failed in execution endpoint [request_id=%s]: errors=%s",
            rid,
            validation.errors,
        )
        raise HTTPException(
            status_code=422,
            detail=ErrorResponse(
                error_code="SQL_VALIDATION_FAILED",
                message="SQL failed safety validation: "
                        + "; ".join(validation.errors),
                request_id=rid,
            ).model_dump(),
        )

    return QueryExecutionResponse(
        sql=result["sql"],
        columns=result["columns"],
        rows=result["rows"],
        row_count=result["row_count"],
        truncated=result["truncated"],
        max_rows=result["max_rows"],
        validation=validation,
        request_id=rid,
    )
