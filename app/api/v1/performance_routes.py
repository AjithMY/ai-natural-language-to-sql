"""
Query performance analysis route for /api/v1/query/performance.

Phase 4E -- User-Triggered Performance Analysis.

Accepts a SQL query and an optional run count. Validates the SQL through
Phase 4A, then benchmarks it by executing it run_count times using
time.perf_counter(). Returns individual timings and aggregate statistics.

Important behavioural constraints:
  - This endpoint is ONLY triggered by an explicit user action ("Analyze Performance").
  - SQL generation does NOT automatically call this endpoint.
  - EXPLAIN does NOT automatically call this endpoint.
  - Optimization does NOT automatically call this endpoint.
  - Only validated SELECT statements are benchmarked.

Security model:
  - SQL must pass Phase 4A validation BEFORE any benchmarking.
  - Only SELECT statements are executed.
  - Error responses never contain credentials, API keys, or stack traces.
  - The Gemini API key is NEVER included in responses or logs.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.models.query_models import (
    QueryPerformanceRequest,
    QueryPerformanceResponse,
)
from app.models.response_models import ErrorResponse
from app.services.query_performance_service import (
    QueryPerformanceError,
    QueryPerformanceService,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Query Performance v1"])


def _request_id(request: Request) -> str | None:
    """Extract the request ID from request state (set by RequestIDMiddleware)."""
    return getattr(request.state, "request_id", None)


@router.post(
    "/query/performance",
    response_model=QueryPerformanceResponse,
    summary="Benchmark a SQL query and return timing statistics (v1)",
    responses={
        422: {"model": ErrorResponse, "description": "SQL failed validation"},
        500: {
            "model": ErrorResponse,
            "description": "Schema retrieval or execution failure",
        },
    },
)
def analyze_performance(
    body: QueryPerformanceRequest,
    request: Request,
) -> QueryPerformanceResponse:
    """Benchmark a validated SELECT query and return timing statistics.

    This endpoint is ONLY triggered by an explicit user action.
    SQL generation, EXPLAIN, and optimization do NOT call this endpoint.

    Workflow:
      1. Validate the SQL using Phase 4A (SQLGlot AST parser + schema check).
      2. If validation fails, return 422 with validation errors -- SQL is NOT executed.
      3. Perform one warmup run (discarded, not included in statistics).
      4. Execute the query run_count times using time.perf_counter().
      5. Calculate average, median, minimum, and maximum timing.
      6. Return all per-run timings and aggregate statistics (in milliseconds).

    Request body::

        {
            "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;",
            "run_count": 5
        }

    Successful response (200)::

        {
            "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;",
            "run_count": 5,
            "warmup_runs": 1,
            "timings_ms": [2.31, 2.18, 2.25, 2.20, 2.22],
            "average_ms": 2.232,
            "median_ms": 2.22,
            "min_ms": 2.18,
            "max_ms": 2.31,
            "validation": {"valid": true, ...},
            "request_id": "..."
        }

    Returns:
        200 QueryPerformanceResponse on success.
        422 ErrorResponse with error_code='SQL_VALIDATION_FAILED' on validation failure.
        500 ErrorResponse with error_code='SCHEMA_RETRIEVAL_FAILED' on DB schema errors.
        500 ErrorResponse with error_code='PERFORMANCE_BENCHMARK_FAILED' on execution errors.
    """
    rid = _request_id(request)

    try:
        service = QueryPerformanceService()
        result = service.benchmark(sql=body.sql, run_count=body.run_count)

    except SQLAlchemyError as exc:
        logger.exception(
            "Schema retrieval failed during performance analysis [request_id=%s]: %s",
            rid,
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error_code="SCHEMA_RETRIEVAL_FAILED",
                message="Failed to retrieve database schema for performance analysis. "
                        "Ensure the database is running.",
                request_id=rid,
            ).model_dump(),
        )

    except QueryPerformanceError as exc:
        safe_message = str(exc)
        logger.error(
            "Performance benchmarking failed [request_id=%s]: %s", rid, safe_message
        )
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error_code="PERFORMANCE_BENCHMARK_FAILED",
                message=safe_message,
                request_id=rid,
            ).model_dump(),
        )

    # Check if validation failed (SQL was NOT benchmarked)
    validation = result["validation"]
    if not validation.valid:
        logger.warning(
            "SQL validation failed in performance endpoint [request_id=%s]: errors=%s",
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

    return QueryPerformanceResponse(
        sql=result["sql"],
        run_count=result["run_count"],
        warmup_runs=result["warmup_runs"],
        timings_ms=result["timings_ms"],
        average_ms=result["average_ms"],
        median_ms=result["median_ms"],
        min_ms=result["min_ms"],
        max_ms=result["max_ms"],
        validation=validation,
        request_id=rid,
    )

