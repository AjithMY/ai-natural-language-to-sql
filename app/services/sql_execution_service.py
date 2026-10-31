"""
SQL Execution service for Phase 4B: Safe SQL Execution.

Responsibilities:
  - Execute only SQL that has already passed Phase 4A validation.
  - Enforce read-only SELECT-only execution.
  - Apply configurable row limits (SQL_MAX_ROWS) to prevent unbounded results.
  - Apply statement-level timeout via MySQL ``max_execution_time`` hint.
  - Return structured results with column names, rows, and truncation info.
  - Handle database errors safely without exposing credentials or internals.

This service intentionally does NOT:
  - Parse or validate SQL (that is done by SQLValidatorService).
  - Perform EXPLAIN or query-plan analysis.
  - Perform query optimization.
  - Execute non-SELECT statements.

Security notes:
  - SQL execution is gated behind Phase 4A validation — unvalidated SQL is
    rejected before reaching this service.
  - The application-layer safety enforces SELECT-only execution even though
    the database user may have broader privileges.  For production, a dedicated
    read-only MySQL user is recommended (documented, not silently configured).
  - All error messages are safe — no credentials, stack traces, or connection
    strings are included in results.
  - NULL values are preserved as Python ``None`` (JSON ``null``).
  - Common MySQL/Python types (int, float, str, Decimal, datetime, date,
    timedelta, bytes) are serialized to JSON-safe representations.
"""

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.core.config import settings
from app.database.connection import get_engine
from app.models.query_models import SQLValidationResult
from app.models.schema_models import DatabaseSchema
from app.services.schema_service import SchemaService
from app.services.sql_validator import SQLValidatorService

logger = logging.getLogger(__name__)


class SQLExecutionError(Exception):
    """Raised when SQL execution encounters a recoverable error.

    The message is safe to include in API error responses.
    """


class SQLExecutionService:
    """Executes validated SELECT queries against the database.

    Usage::

        service = SQLExecutionService()
        result = service.execute("SELECT name, marks FROM students;")
    """

    def __init__(self) -> None:
        self._schema_service = SchemaService()
        self._validator = SQLValidatorService()
        self._max_rows = settings.SQL_MAX_ROWS
        self._timeout_seconds = settings.SQL_QUERY_TIMEOUT_SECONDS

    def execute(self, sql: str) -> dict[str, Any]:
        """Validate and execute a SQL query.

        Workflow:
          1. Retrieve the live database schema.
          2. Validate the SQL using Phase 4A SQLValidatorService.
          3. If validation fails, return the validation result WITHOUT executing.
          4. If validation passes, execute the query with row limits and timeout.
          5. Return structured results.

        Args:
            sql: The SQL query string to validate and execute.

        Returns:
            A dict with keys: sql, columns, rows, row_count, truncated,
            max_rows, validation.

        Raises:
            SQLAlchemyError: If schema retrieval fails.
            SQLExecutionError: If query execution fails (message is safe
                for API responses).
        """
        logger.info(
            "SQLExecutionService: starting execution [sql_length=%d chars]",
            len(sql),
        )

        # Step 1: Retrieve live schema
        schema = self._schema_service.get_full_schema()

        # Step 2: Validate
        validation_result = self._validator.validate(sql, schema)

        # Step 3: If validation fails, return immediately
        if not validation_result.valid:
            logger.warning(
                "SQLExecutionService: validation FAILED, not executing [errors=%s]",
                validation_result.errors,
            )
            return {
                "sql": sql,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "max_rows": self._max_rows,
                "validation": validation_result,
            }

        # Step 4: Execute the validated query
        columns, rows, truncated = self._execute_query(sql)

        logger.info(
            "SQLExecutionService: execution complete [rows=%d, truncated=%s]",
            len(rows),
            truncated,
        )

        return {
            "sql": sql,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "max_rows": self._max_rows,
            "validation": validation_result,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _execute_query(self, sql: str) -> tuple[list[str], list[list], bool]:
        """Execute a validated SELECT query with row limits and timeout.

        Args:
            sql: Validated SQL string (must be a single SELECT).

        Returns:
            (columns, rows, truncated) where:
              - columns: list of column name strings
              - rows: list of row lists (JSON-safe values)
              - truncated: True if result set exceeded max_rows

        Raises:
            SQLExecutionError: On any database execution failure.
        """
        engine = get_engine()

        # Fetch max_rows + 1 to detect if truncation is needed
        fetch_limit = self._max_rows + 1

        try:
            with engine.connect() as conn:
                # Set MySQL statement timeout (max_execution_time is in milliseconds)
                timeout_ms = self._timeout_seconds * 1000
                conn.execute(
                    text(f"SET SESSION MAX_EXECUTION_TIME = {timeout_ms}")
                )

                result = conn.execute(text(sql))

                # Extract column names from the result cursor
                columns = list(result.keys())

                # Fetch up to fetch_limit rows
                raw_rows = result.fetchmany(fetch_limit)

                # Check for truncation
                truncated = len(raw_rows) > self._max_rows
                if truncated:
                    raw_rows = raw_rows[: self._max_rows]

                # Serialize rows to JSON-safe format
                rows = [
                    [self._serialize_value(val) for val in row]
                    for row in raw_rows
                ]

                return columns, rows, truncated

        except OperationalError as exc:
            error_str = str(exc).lower()
            if "max_execution_time" in error_str or "time" in error_str:
                logger.error(
                    "SQL execution timed out [timeout=%ds]: %s",
                    self._timeout_seconds,
                    type(exc).__name__,
                )
                raise SQLExecutionError(
                    f"Query execution timed out after {self._timeout_seconds} seconds. "
                    "Try simplifying your query or adding more specific conditions."
                ) from exc
            logger.error(
                "SQL execution operational error: %s", type(exc).__name__
            )
            raise SQLExecutionError(
                "Database execution failed. Ensure the database is running "
                "and the query is compatible with the current schema."
            ) from exc

        except SQLAlchemyError as exc:
            logger.error(
                "SQL execution error: %s", type(exc).__name__
            )
            raise SQLExecutionError(
                "An unexpected database error occurred during query execution. "
                "Please try again."
            ) from exc

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        """Convert a database value to a JSON-serializable Python type.

        Handles:
          - None (NULL) → None
          - int, float, str, bool → pass through
          - Decimal → float
          - datetime → ISO format string
          - date → ISO format string
          - time → ISO format string
          - timedelta → total seconds as float
          - bytes → hex string
          - Everything else → str()

        Args:
            value: A raw value from a SQLAlchemy result row.

        Returns:
            A JSON-serializable Python value.
        """
        if value is None:
            return None
        if isinstance(value, (int, float, str, bool)):
            return value
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, time):
            return value.isoformat()
        if isinstance(value, timedelta):
            return value.total_seconds()
        if isinstance(value, bytes):
            return value.hex()
        # Fallback: convert to string
        return str(value)
