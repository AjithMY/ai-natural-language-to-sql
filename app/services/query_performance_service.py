"""
Query Performance Analysis service for Phase 4E: User-Triggered Performance Analysis.

Responsibilities:
  - Validate SQL using Phase 4A SQLValidatorService.
  - Reject non-SELECT statements, multi-statement SQL, and injection attempts.
  - Execute the validated SELECT multiple times using time.perf_counter().
  - Optionally perform warmup runs BEFORE timed runs; warmup times are NOT
    included in the reported statistics.
  - Calculate aggregate statistics: average, median, minimum, maximum.
  - Return all per-run timings plus aggregates (all in milliseconds).

This service intentionally does NOT:
  - Execute SQL that has not passed Phase 4A validation.
  - Execute non-SELECT statements.
  - Automatically trigger benchmarking during SQL generation.
  - Automatically trigger benchmarking during EXPLAIN or optimization.
  - Modify the database schema or data.
  - Invent or fabricate timing values.

Security notes:
  - SQL must pass Phase 4A validation before any execution.
  - All reported timing values are real measurements from time.perf_counter().
  - Error messages are safe for API responses -- no credentials or stack traces.
"""

import logging
import statistics
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.database.connection import get_engine
from app.services.schema_service import SchemaService
from app.services.sql_validator import SQLValidatorService

logger = logging.getLogger(__name__)

# Default number of warmup runs executed before timed benchmark runs.
# Warmup runs are discarded and NOT included in statistics.
_DEFAULT_WARMUP_RUNS: int = 1


class QueryPerformanceError(Exception):
    """Raised when benchmarking encounters a recoverable error.

    The message is safe to include in API error responses.
    """


class QueryPerformanceService:
    """Benchmarks validated SELECT queries using real high-resolution timings.

    Usage::

        service = QueryPerformanceService()
        result = service.benchmark(
            sql="SELECT name, marks FROM students;",
            run_count=5,
        )
    """

    def __init__(self, warmup_runs: int = _DEFAULT_WARMUP_RUNS) -> None:
        self._schema_service = SchemaService()
        self._validator = SQLValidatorService()
        self._warmup_runs = warmup_runs

    def benchmark(self, sql: str, run_count: int = 5) -> dict[str, Any]:
        """Validate and benchmark a SQL SELECT query.

        Workflow:
          1. Retrieve the live database schema.
          2. Validate the SQL using Phase 4A SQLValidatorService.
          3. If validation fails, return the validation result WITHOUT executing.
          4. Perform warmup run(s) -- results are discarded.
          5. Perform run_count timed benchmark runs using time.perf_counter().
          6. Calculate and return aggregate statistics.

        Args:
            sql:       The SQL query string to validate and benchmark.
            run_count: Number of timed benchmark runs (default 5, range 1-20).

        Returns:
            A dict with keys:
              sql, run_count, warmup_runs, timings_ms, average_ms,
              median_ms, min_ms, max_ms, validation.

        Raises:
            SQLAlchemyError:       If schema retrieval fails.
            QueryPerformanceError: If query execution fails during benchmarking
                                   (message is safe for API responses).
        """
        logger.info(
            "QueryPerformanceService: starting benchmark "
            "[sql_length=%d, run_count=%d, warmup=%d]",
            len(sql),
            run_count,
            self._warmup_runs,
        )

        # Step 1: Retrieve live schema
        schema = self._schema_service.get_full_schema()

        # Step 2: Validate SQL
        validation_result = self._validator.validate(sql, schema)

        # Step 3: If validation fails, return without executing
        if not validation_result.valid:
            logger.warning(
                "QueryPerformanceService: validation FAILED [errors=%s]",
                validation_result.errors,
            )
            return {
                "sql": sql,
                "run_count": 0,
                "warmup_runs": 0,
                "timings_ms": [],
                "average_ms": 0.0,
                "median_ms": 0.0,
                "min_ms": 0.0,
                "max_ms": 0.0,
                "validation": validation_result,
            }

        # Step 4: Warmup runs (discarded -- not counted in statistics)
        if self._warmup_runs > 0:
            logger.debug(
                "QueryPerformanceService: running %d warmup run(s)", self._warmup_runs
            )
            for _ in range(self._warmup_runs):
                self._execute_once(sql)

        # Step 5: Timed benchmark runs
        timings_ms: list[float] = []
        for run_idx in range(run_count):
            elapsed_ms = self._execute_once(sql)
            timings_ms.append(elapsed_ms)
            logger.debug(
                "QueryPerformanceService: run %d/%d -- %.4f ms",
                run_idx + 1,
                run_count,
                elapsed_ms,
            )

        # Step 6: Calculate statistics
        avg_ms = sum(timings_ms) / len(timings_ms)
        med_ms = statistics.median(timings_ms)
        min_ms = min(timings_ms)
        max_ms = max(timings_ms)

        logger.info(
            "QueryPerformanceService: benchmark complete "
            "[runs=%d, avg=%.4f ms, min=%.4f ms, max=%.4f ms]",
            run_count,
            avg_ms,
            min_ms,
            max_ms,
        )

        return {
            "sql": sql,
            "run_count": run_count,
            "warmup_runs": self._warmup_runs,
            "timings_ms": timings_ms,
            "average_ms": avg_ms,
            "median_ms": med_ms,
            "min_ms": min_ms,
            "max_ms": max_ms,
            "validation": validation_result,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _execute_once(self, sql: str) -> float:
        """Execute a validated SELECT query once and return the wall time in ms.

        Uses time.perf_counter() for high-resolution timing.
        Timing begins immediately before the SQLAlchemy execute call and
        ends immediately after all rows are fetched so MySQL fully processes
        the query.

        Args:
            sql: Validated SQL string (must be a single SELECT).

        Returns:
            Elapsed time in milliseconds (float).

        Raises:
            QueryPerformanceError: On any database execution failure.
        """
        engine = get_engine()

        try:
            with engine.connect() as conn:
                start = time.perf_counter()
                result = conn.execute(text(sql))
                # Consume all rows so MySQL fully processes the query
                result.fetchall()
                end = time.perf_counter()

            return (end - start) * 1_000  # convert seconds to milliseconds

        except OperationalError as exc:
            error_str = str(exc).lower()
            if "max_execution_time" in error_str or (
                "query" in error_str and "time" in error_str
            ):
                logger.error(
                    "QueryPerformanceService: execution timed out: %s",
                    type(exc).__name__,
                )
                raise QueryPerformanceError(
                    "Query execution timed out during benchmarking. "
                    "Try simplifying your query or adding more specific conditions."
                ) from exc
            logger.error(
                "QueryPerformanceService: operational error: %s", type(exc).__name__
            )
            raise QueryPerformanceError(
                "Database execution failed during benchmarking. Ensure the database "
                "is running and the query is compatible with the current schema."
            ) from exc

        except SQLAlchemyError as exc:
            logger.error(
                "QueryPerformanceService: unexpected DB error: %s", type(exc).__name__
            )
            raise QueryPerformanceError(
                "An unexpected database error occurred during benchmarking. "
                "Please try again."
            ) from exc

