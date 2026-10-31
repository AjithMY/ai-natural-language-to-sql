"""
Query optimization route for /api/v1/query/optimize.

Phase 4D — SQL Query Optimization.

Accepts a SQL query, validates it through Phase 4A, applies deterministic
rule-based optimizations, re-validates the optimized candidate, and returns
both the original and optimized SQL with explanations.

Security model:
  - SQL must pass Phase 4A validation BEFORE optimization.
  - The optimized SQL is re-validated before being returned to the client.
  - Neither the original nor the optimized SQL is executed.
  - Error responses never contain credentials, API keys, or stack traces.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.models.query_models import (
    QueryOptimizeRequest,
    QueryOptimizeResponse,
)
from app.models.response_models import ErrorResponse
from app.services.query_optimization_service import (
    QueryOptimizationError,
    QueryOptimizationService,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Query Optimization v1"])


def _request_id(request: Request) -> str | None:
    """Extract the request ID from request state (set by RequestIDMiddleware)."""
    return getattr(request.state, "request_id", None)


@router.post(
    "/query/optimize",
    response_model=QueryOptimizeResponse,
    summary="Validate and optimize a SQL query (v1)",
    responses={
        422: {"model": ErrorResponse, "description": "SQL failed validation"},
        500: {
            "model": ErrorResponse,
            "description": "Schema retrieval or optimization failure",
        },
    },
)
def optimize_sql_query(
    body: QueryOptimizeRequest,
    request: Request,
) -> QueryOptimizeResponse:
    """Validate a SQL SELECT query and return an optimized candidate.

    Workflow:
      1. Validate the SQL using Phase 4A (SQLGlot AST parser + schema check).
      2. If validation fails, return 422 with errors — no optimization occurs.
      3. Apply deterministic rule-based optimizations.
      4. Re-validate the optimized SQL.
      5. Return both original and optimized SQL with explanations.

    Neither the original nor the optimized SQL is executed.  The optimized
    query is a candidate only — performance is not measured in this phase.

    Returns:
        200 QueryOptimizeResponse on success.
        422 ErrorResponse with error_code='SQL_VALIDATION_FAILED'.
        500 ErrorResponse with error_code='SCHEMA_RETRIEVAL_FAILED'.
        500 ErrorResponse with error_code='OPTIMIZATION_FAILED'.
    """
    rid = _request_id(request)

    try:
        service = QueryOptimizationService()
        result = service.optimize(body.sql)

    except SQLAlchemyError as exc:
        logger.exception(
            "Schema retrieval failed during optimization [request_id=%s]: %s",
            rid,
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error_code="SCHEMA_RETRIEVAL_FAILED",
                message="Failed to retrieve database schema for query optimization. "
                        "Ensure the database is running.",
                request_id=rid,
            ).model_dump(),
        )

    except QueryOptimizationError as exc:
        safe_message = str(exc)
        logger.error(
            "Optimization failed [request_id=%s]: %s", rid, safe_message
        )
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error_code="OPTIMIZATION_FAILED",
                message=safe_message,
                request_id=rid,
            ).model_dump(),
        )

    # Check if original SQL validation failed
    validation = result["validation"]
    if not validation.valid:
        logger.warning(
            "SQL validation failed in optimize endpoint [request_id=%s]: "
            "errors=%s",
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

    return QueryOptimizeResponse(
        original_sql=result["original_sql"],
        optimized_sql=result["optimized_sql"],
        changed=result["changed"],
        optimizations=result["optimizations"],
        index_recommendations=result["index_recommendations"],
        validation=validation,
        optimized_validation=result["optimized_validation"],
        request_id=rid,
    )
