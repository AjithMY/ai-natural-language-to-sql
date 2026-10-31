"""
Query Plan service for Phase 4C: MySQL Query Plan Analysis.

Responsibilities:
  - Accept a SQL query string.
  - Validate it through Phase 4A (SQLValidatorService).
  - If validation passes, obtain the MySQL EXPLAIN plan.
  - Parse the EXPLAIN output into structured ExplainPlanRow objects.
  - Generate descriptive analysis observations from the plan.
  - Return structured results.
  - Handle database errors safely without exposing credentials or internals.

This service intentionally does NOT:
  - Execute the original SELECT query.
  - Perform query optimization or SQL rewriting.
  - Measure execution time or performance.
  - Recommend index changes.
  - Call Gemini or any AI service.

Security notes:
  - SQL must pass Phase 4A validation BEFORE EXPLAIN is called.
  - Unvalidated SQL never reaches the database.
  - Only ``EXPLAIN SELECT …`` is sent — the original SELECT is never executed.
  - All error messages are safe — no credentials, stack traces, or connection
    strings are included in results.
"""

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.database.connection import get_engine
from app.models.query_models import ExplainPlanRow, SQLValidationResult
from app.services.schema_service import SchemaService
from app.services.sql_validator import SQLValidatorService

logger = logging.getLogger(__name__)


class QueryPlanError(Exception):
    """Raised when EXPLAIN analysis encounters a recoverable error.

    The message is safe to include in API error responses.
    """


class QueryPlanService:
    """Obtains and analyzes MySQL EXPLAIN plans for validated SELECT queries.

    Usage::

        service = QueryPlanService()
        result = service.explain("SELECT name, marks FROM students;")
    """

    def __init__(self) -> None:
        self._schema_service = SchemaService()
        self._validator = SQLValidatorService()

    def explain(self, sql: str) -> dict[str, Any]:
        """Validate SQL and obtain its MySQL EXPLAIN plan.

        Workflow:
          1. Retrieve the live database schema.
          2. Validate the SQL using Phase 4A SQLValidatorService.
          3. If validation fails, return the validation result WITHOUT
             calling EXPLAIN.
          4. If validation passes, run ``EXPLAIN <sql>`` against MySQL.
          5. Parse the EXPLAIN output into ExplainPlanRow objects.
          6. Generate descriptive analysis observations.
          7. Return structured results.

        Args:
            sql: The SQL query string to validate and analyze.

        Returns:
            A dict with keys: sql, plan, analysis, validation.

        Raises:
            SQLAlchemyError: If schema retrieval fails.
            QueryPlanError: If EXPLAIN execution fails (message is safe
                for API responses).
        """
        logger.info(
            "QueryPlanService: starting EXPLAIN analysis [sql_length=%d chars]",
            len(sql),
        )

        # Step 1: Retrieve live schema
        schema = self._schema_service.get_full_schema()

        # Step 2: Validate
        validation_result = self._validator.validate(sql, schema)

        # Step 3: If validation fails, return immediately — no EXPLAIN
        if not validation_result.valid:
            logger.warning(
                "QueryPlanService: validation FAILED, not running EXPLAIN [errors=%s]",
                validation_result.errors,
            )
            return {
                "sql": sql,
                "plan": [],
                "analysis": [],
                "validation": validation_result,
            }

        # Step 4: Run EXPLAIN
        plan_rows = self._run_explain(sql)

        # Step 5–6: Generate analysis observations
        analysis = self._analyze_plan(plan_rows)

        logger.info(
            "QueryPlanService: EXPLAIN complete [plan_rows=%d, observations=%d]",
            len(plan_rows),
            len(analysis),
        )

        return {
            "sql": sql,
            "plan": plan_rows,
            "analysis": analysis,
            "validation": validation_result,
        }

    # Alias for explain() to maintain compatibility with analysis callers and boundary tests
    analyze = explain

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_explain(self, sql: str) -> list[ExplainPlanRow]:
        """Execute ``EXPLAIN <sql>`` and parse the result into ExplainPlanRow objects.

        Args:
            sql: Validated SQL string (must be a single SELECT).

        Returns:
            A list of ExplainPlanRow objects, one per EXPLAIN output row.

        Raises:
            QueryPlanError: On any database failure during EXPLAIN.
        """
        engine = get_engine()

        # Strip trailing semicolons for clean EXPLAIN statement construction
        clean_sql = sql.rstrip().rstrip(";")
        explain_sql = f"EXPLAIN {clean_sql}"

        try:
            with engine.connect() as conn:
                result = conn.execute(text(explain_sql))

                # Get column names from the result
                columns = [c.lower() for c in result.keys()]

                rows: list[ExplainPlanRow] = []
                for raw_row in result.fetchall():
                    # Build a dict from column names → values
                    row_dict = dict(zip(columns, raw_row))
                    rows.append(self._parse_explain_row(row_dict))

                return rows

        except OperationalError as exc:
            logger.error(
                "EXPLAIN execution operational error: %s", type(exc).__name__
            )
            raise QueryPlanError(
                "Failed to obtain query execution plan. "
                "Ensure the database is running and the query is "
                "compatible with the current schema."
            ) from exc

        except SQLAlchemyError as exc:
            logger.error(
                "EXPLAIN execution error: %s", type(exc).__name__
            )
            raise QueryPlanError(
                "An unexpected database error occurred during EXPLAIN analysis. "
                "Please try again."
            ) from exc

    @staticmethod
    def _parse_explain_row(row_dict: dict[str, Any]) -> ExplainPlanRow:
        """Parse a raw EXPLAIN result dict into an ExplainPlanRow.

        Handles MySQL column name variations and safely converts types.
        NULL values are preserved as None.
        """
        def _safe_int(value: Any) -> int | None:
            if value is None:
                return None
            try:
                return int(value)
            except (ValueError, TypeError):
                return None

        def _safe_float(value: Any) -> float | None:
            if value is None:
                return None
            try:
                return float(value)
            except (ValueError, TypeError):
                return None

        def _safe_str(value: Any) -> str | None:
            if value is None:
                return None
            return str(value)

        return ExplainPlanRow(
            id=_safe_int(row_dict.get("id")),
            select_type=_safe_str(row_dict.get("select_type")),
            table=_safe_str(row_dict.get("table")),
            partitions=_safe_str(row_dict.get("partitions")),
            type=_safe_str(row_dict.get("type")),
            possible_keys=_safe_str(row_dict.get("possible_keys")),
            key=_safe_str(row_dict.get("key")),
            key_len=_safe_str(row_dict.get("key_len")),
            ref=_safe_str(row_dict.get("ref")),
            rows=_safe_int(row_dict.get("rows")),
            filtered=_safe_float(row_dict.get("filtered")),
            extra=_safe_str(row_dict.get("extra", row_dict.get("Extra"))),
        )

    @staticmethod
    def _analyze_plan(plan_rows: list[ExplainPlanRow]) -> list[str]:
        """Generate descriptive observations from the EXPLAIN plan.

        These are analysis-only observations — no optimization suggestions
        or SQL rewrites are produced.

        Args:
            plan_rows: Parsed EXPLAIN plan rows.

        Returns:
            A list of human-readable observation strings.
        """
        observations: list[str] = []

        for row in plan_rows:
            table_name = row.table or "unknown"

            # Access type observations
            if row.type is not None:
                access_type = row.type.upper()

                if access_type == "ALL":
                    observations.append(f"Full table scan on '{table_name}'")
                elif access_type == "INDEX":
                    key_info = f" using '{row.key}'" if row.key else ""
                    observations.append(f"Full index scan on '{table_name}'{key_info}")
                elif access_type == "RANGE":
                    key_info = f" using '{row.key}'" if row.key else ""
                    observations.append(f"Range scan on '{table_name}'{key_info}")
                elif access_type == "REF":
                    key_info = f" using '{row.key}'" if row.key else ""
                    observations.append(f"Index lookup on '{table_name}'{key_info}")
                elif access_type == "EQ_REF":
                    key_info = f" using '{row.key}'" if row.key else ""
                    observations.append(f"Unique index lookup on '{table_name}'{key_info}")
                elif access_type == "CONST" or access_type == "SYSTEM":
                    observations.append(f"Constant lookup on '{table_name}' (at most one row)")
                elif access_type == "REF_OR_NULL":
                    key_info = f" using '{row.key}'" if row.key else ""
                    observations.append(f"Index lookup with NULL check on '{table_name}'{key_info}")

            # Index observations
            if row.key is not None and row.type and row.type.upper() not in (
                "INDEX", "RANGE", "REF", "EQ_REF", "CONST", "SYSTEM", "REF_OR_NULL",
            ):
                observations.append(f"Index '{row.key}' used on '{table_name}'")

            # Extra field observations
            if row.extra:
                extra_lower = row.extra.lower()
                if "using filesort" in extra_lower:
                    observations.append("Using filesort")
                if "using temporary" in extra_lower:
                    observations.append("Using temporary table")
                if "using index" in extra_lower and "using index condition" not in extra_lower:
                    observations.append(f"Covering index on '{table_name}' (no table access needed)")
                if "using index condition" in extra_lower:
                    observations.append(f"Index condition pushdown on '{table_name}'")
                if "using where" in extra_lower:
                    observations.append(f"Row filtering with WHERE on '{table_name}'")

            # Estimated rows
            if row.rows is not None and row.table is not None:
                observations.append(
                    f"{row.rows} estimated rows scanned on '{table_name}'"
                )

        return observations
