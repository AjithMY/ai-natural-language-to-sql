"""
Query EXPLAIN route for /api/v1/query/explain.

Phase 4C — MySQL Query Plan Analysis.

Accepts a SQL query, validates it through Phase 4A, and if validation
passes, obtains the MySQL EXPLAIN plan and returns structured analysis.

Security model:
  - SQL must pass Phase 4A validation BEFORE EXPLAIN is called.
  - Only ``EXPLAIN SELECT …`` is sent to MySQL — the original SELECT
    is NOT executed.
  - Error responses never contain credentials, API keys, or stack traces.
  - The Gemini API key is NEVER included in responses or logs.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.models.query_models import (
    QueryExplainRequest,
    QueryExplainResponse,
)
from app.models.response_models import ErrorResponse
from app.services.query_plan_service import QueryPlanError, QueryPlanService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Query Plan Analysis v1"])


def _request_id(request: Request) -> str | None:
    """Extract the request ID from request state (set by RequestIDMiddleware)."""
    return getattr(request.state, "request_id", None)


@router.post(
    "/query/explain",
    response_model=QueryExplainResponse,
    summary="Validate and obtain the EXPLAIN plan for a SQL query (v1)",
    responses={
        422: {"model": ErrorResponse, "description": "SQL failed validation"},
        500: {
            "model": ErrorResponse,
            "description": "Schema retrieval or EXPLAIN failure",
        },
    },
)
def explain_sql_query(
    body: QueryExplainRequest,
    request: Request,
) -> QueryExplainResponse:
    """Validate a SQL SELECT query and return its MySQL EXPLAIN plan.

    Workflow:
      1. Validate the SQL using Phase 4A (SQLGlot AST parser + schema check).
      2. If validation fails, return 422 with validation errors — EXPLAIN is
         NOT performed.
      3. If validation passes, run ``EXPLAIN <sql>`` against MySQL.
      4. Parse the EXPLAIN output and generate analysis observations.
      5. Return structured plan results.

    The original SELECT query is NOT executed. Only ``EXPLAIN SELECT …``
    is sent to MySQL for plan analysis.

    Request body::

        {"sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;"}

    Successful response (200)::

        {
            "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;",
            "plan": [...],
            "analysis": ["Full table scan on 'students'", ...],
            "validation": {"valid": true, ...},
            "request_id": "..."
        }

    Returns:
        200 QueryExplainResponse on success.
        422 ErrorResponse with error_code='SQL_VALIDATION_FAILED' on validation failure.
        500 ErrorResponse with error_code='SCHEMA_RETRIEVAL_FAILED' on DB schema errors.
        500 ErrorResponse with error_code='EXPLAIN_FAILED' on EXPLAIN errors.
    """
    rid = _request_id(request)

    try:
        service = QueryPlanService()
        result = service.explain(body.sql)

    except SQLAlchemyError as exc:
        logger.exception(
            "Schema retrieval failed during EXPLAIN [request_id=%s]: %s",
            rid,
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error_code="SCHEMA_RETRIEVAL_FAILED",
                message="Failed to retrieve database schema for query plan analysis. "
                        "Ensure the database is running.",
                request_id=rid,
            ).model_dump(),
        )

    except QueryPlanError as exc:
        safe_message = str(exc)
        logger.error(
            "EXPLAIN failed [request_id=%s]: %s", rid, safe_message
        )
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error_code="EXPLAIN_FAILED",
                message=safe_message,
                request_id=rid,
            ).model_dump(),
        )

    # Check if validation failed (EXPLAIN was NOT performed)
    validation = result["validation"]
    if not validation.valid:
        logger.warning(
            "SQL validation failed in EXPLAIN endpoint [request_id=%s]: errors=%s",
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

    return QueryExplainResponse(
        sql=result["sql"],
        plan=result["plan"],
        analysis=result["analysis"],
        validation=validation,
        request_id=rid,
    )
