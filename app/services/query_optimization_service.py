"""
Query Optimization service for Phase 4D: SQL Query Optimization.

Implements a deterministic, rule-based SQL optimizer using SQLGlot AST
transformations.  Each rule is applied only when semantic equivalence
can be proven.

Workflow:
  1. Validate original SQL via Phase 4A.
  2. Parse into SQLGlot AST.
  3. Apply optimization rules in order.
  4. Generate optimized SQL from modified AST.
  5. Re-validate the optimized SQL via Phase 4A.
  6. Return original + optimized + explanations.

This service intentionally does NOT:
  - Execute any SQL query (original or optimized).
  - Create or modify database indexes or schema.
  - Call Gemini or any AI service.
  - Measure execution time or performance.
  - Claim that optimized queries are faster (unmeasured).

Security notes:
  - Original SQL must pass Phase 4A validation before any optimization.
  - Optimized SQL is re-validated — unsafe candidates are discarded.
  - All error messages are safe for API responses.
"""

import logging
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.models.query_models import OptimizationApplied, SQLValidationResult
from app.models.schema_models import DatabaseSchema, TableSchema
from app.services.schema_service import SchemaService
from app.services.sql_validator import SQLValidatorService

logger = logging.getLogger(__name__)


class QueryOptimizationError(Exception):
    """Raised when query optimization encounters a recoverable error.

    The message is safe to include in API error responses.
    """


class QueryOptimizationService:
    """Deterministic, rule-based SQL optimizer.

    Usage::

        service = QueryOptimizationService()
        result = service.optimize("SELECT * FROM students;")
    """

    def __init__(self) -> None:
        self._schema_service = SchemaService()
        self._validator = SQLValidatorService()

    def optimize(self, sql: str) -> dict[str, Any]:
        """Validate and optimize a SQL query.

        Args:
            sql: The SQL query string to validate and optimize.

        Returns:
            A dict with keys: original_sql, optimized_sql, changed,
            optimizations, index_recommendations, validation,
            optimized_validation.

        Raises:
            SQLAlchemyError: If schema retrieval fails.
            QueryOptimizationError: If optimization encounters an error.
        """
        logger.info(
            "QueryOptimizationService: starting optimization [sql_length=%d]",
            len(sql),
        )

        # Step 1: Retrieve live schema
        schema = self._schema_service.get_full_schema()

        # Step 2: Validate original SQL via Phase 4A
        original_validation = self._validator.validate(sql, schema)

        if not original_validation.valid:
            logger.warning(
                "QueryOptimizationService: validation FAILED, not optimizing "
                "[errors=%s]",
                original_validation.errors,
            )
            return {
                "original_sql": sql,
                "optimized_sql": sql,
                "changed": False,
                "optimizations": [],
                "index_recommendations": [],
                "validation": original_validation,
                "optimized_validation": None,
            }

        # Step 3: Parse SQL into AST
        try:
            ast = sqlglot.parse(sql, dialect="mysql")[0]
        except (ParseError, IndexError) as exc:
            logger.error("AST parse failed during optimization: %s", exc)
            raise QueryOptimizationError(
                "Failed to parse SQL for optimization analysis."
            ) from exc

        # Step 4: Build schema lookup for optimization rules
        table_lookup = self._build_table_lookup(schema)

        # Step 5: Apply optimization rules
        optimizations: list[OptimizationApplied] = []
        index_recommendations: list[str] = []

        self._try_expand_select_star(ast, table_lookup, optimizations)
        self._try_remove_redundant_distinct(ast, table_lookup, optimizations)
        self._try_remove_tautology(ast, optimizations)
        self._try_detect_constant_false(ast, optimizations)
        self._generate_index_recommendations(ast, table_lookup, index_recommendations)

        # Step 6: Generate optimized SQL
        optimized_sql = ast.sql(dialect="mysql")

        # Determine if anything changed
        changed = optimized_sql.strip().rstrip(";") != sql.strip().rstrip(";")

        # Step 7: Re-validate optimized SQL if it changed
        optimized_validation = None
        if changed:
            optimized_validation = self._validator.validate(optimized_sql, schema)

            # If optimized SQL fails validation, discard it
            if not optimized_validation.valid:
                logger.warning(
                    "QueryOptimizationService: optimized SQL failed validation, "
                    "discarding. errors=%s",
                    optimized_validation.errors,
                )
                return {
                    "original_sql": sql,
                    "optimized_sql": sql,
                    "changed": False,
                    "optimizations": [],
                    "index_recommendations": index_recommendations,
                    "validation": original_validation,
                    "optimized_validation": optimized_validation,
                }

        if not changed:
            optimizations = [
                o for o in optimizations
                if o.rule == "constant_false_detection"
            ]

        logger.info(
            "QueryOptimizationService: complete [changed=%s, rules=%d, "
            "index_recs=%d]",
            changed,
            len(optimizations),
            len(index_recommendations),
        )

        return {
            "original_sql": sql,
            "optimized_sql": optimized_sql if changed else sql,
            "changed": changed,
            "optimizations": optimizations,
            "index_recommendations": index_recommendations,
            "validation": original_validation,
            "optimized_validation": optimized_validation,
        }

    # ------------------------------------------------------------------
    # Schema helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_table_lookup(
        schema: DatabaseSchema,
    ) -> dict[str, TableSchema]:
        """Build a lowercase table-name → TableSchema lookup."""
        return {t.name.lower(): t for t in schema.tables}

    @staticmethod
    def _resolve_table_name(
        ast: exp.Select,
        table_lookup: dict[str, TableSchema],
    ) -> TableSchema | None:
        """Resolve the single FROM table in a SELECT, handling aliases.

        Returns the TableSchema if a single table is found, else None.
        """
        from_clause = ast.find(exp.From)
        if from_clause is None:
            return None

        # Get all tables in FROM (including JOINs)
        tables = list(ast.find_all(exp.Table))
        if len(tables) != 1:
            # Multi-table queries — don't apply single-table rules
            return None

        table_node = tables[0]
        table_name = table_node.name.lower()
        return table_lookup.get(table_name)

    # ------------------------------------------------------------------
    # Optimization Rule 1: SELECT * expansion
    # ------------------------------------------------------------------

    def _try_expand_select_star(
        self,
        ast: exp.Select,
        table_lookup: dict[str, TableSchema],
        optimizations: list[OptimizationApplied],
    ) -> None:
        """Expand SELECT * to explicit column list when a single table is used.

        Only applies when:
          - The SELECT contains a Star (*) expression
          - There is exactly one table in FROM (no JOINs)
          - The table exists in the schema
        """
        stars = list(ast.find_all(exp.Star))
        if not stars:
            return

        table_schema = self._resolve_table_name(ast, table_lookup)
        if table_schema is None:
            return

        # Get column names from schema
        col_names = [col.name for col in table_schema.columns]

        # Build new column expressions
        new_exprs = [
            exp.Column(this=exp.to_identifier(c))
            for c in col_names
        ]

        original_fragment = "SELECT *"
        optimized_fragment = "SELECT " + ", ".join(col_names)

        ast.set("expressions", new_exprs)

        optimizations.append(OptimizationApplied(
            rule="select_star_expansion",
            description=(
                f"Expanded SELECT * to explicit column list "
                f"({len(col_names)} columns from '{table_schema.name}'). "
                f"Explicit columns help the query planner and improve clarity."
            ),
            original_fragment=original_fragment,
            optimized_fragment=optimized_fragment,
        ))

    # ------------------------------------------------------------------
    # Optimization Rule 2: Redundant DISTINCT removal
    # ------------------------------------------------------------------

    def _try_remove_redundant_distinct(
        self,
        ast: exp.Select,
        table_lookup: dict[str, TableSchema],
        optimizations: list[OptimizationApplied],
    ) -> None:
        """Remove DISTINCT when the selected columns include all primary
        key columns of the single source table.

        DISTINCT is redundant when the result is already guaranteed to
        be unique by the primary key constraint.
        """
        # Check if DISTINCT is set
        if not ast.args.get("distinct"):
            return

        table_schema = self._resolve_table_name(ast, table_lookup)
        if table_schema is None:
            return

        if not table_schema.primary_keys:
            return

        # Get selected column names (lowercase)
        selected_cols: set[str] = set()
        for expr in ast.expressions:
            if isinstance(expr, exp.Column):
                selected_cols.add(expr.name.lower())
            elif isinstance(expr, exp.Alias):
                inner = expr.this
                if isinstance(inner, exp.Column):
                    selected_cols.add(inner.name.lower())

        # Check if all PK columns are selected
        pk_cols = {pk.lower() for pk in table_schema.primary_keys}
        if pk_cols.issubset(selected_cols):
            ast.set("distinct", None)

            optimizations.append(OptimizationApplied(
                rule="redundant_distinct_removal",
                description=(
                    f"Removed DISTINCT — the selected columns include the "
                    f"primary key ({', '.join(table_schema.primary_keys)}) "
                    f"of '{table_schema.name}', which already guarantees "
                    f"row uniqueness."
                ),
                original_fragment="SELECT DISTINCT",
                optimized_fragment="SELECT",
            ))

    # ------------------------------------------------------------------
    # Optimization Rule 3: Tautology removal
    # ------------------------------------------------------------------

    def _try_remove_tautology(
        self,
        ast: exp.Select,
        optimizations: list[OptimizationApplied],
    ) -> None:
        """Remove tautological WHERE conditions: 1=1, TRUE, 1.

        If the entire WHERE clause is a tautology, it is removed.
        If a tautology appears as part of an AND chain, only the
        tautological branch is removed.
        """
        where = ast.find(exp.Where)
        if where is None:
            return

        condition = where.this

        if self._is_tautology(condition):
            # Entire WHERE is a tautology — remove it
            ast.set("where", None)
            optimizations.append(OptimizationApplied(
                rule="tautology_removal",
                description=(
                    "Removed tautological WHERE clause "
                    f"('{condition.sql(dialect='mysql')}' is always true)."
                ),
                original_fragment=f"WHERE {condition.sql(dialect='mysql')}",
                optimized_fragment=None,
            ))
            return

        # Check for tautology in AND chain
        if isinstance(condition, exp.And):
            cleaned = self._remove_tautology_from_and(condition)
            if cleaned is not condition:
                original_where = f"WHERE {condition.sql(dialect='mysql')}"
                if cleaned is None:
                    ast.set("where", None)
                    optimized_where = None
                else:
                    where.set("this", cleaned)
                    optimized_where = f"WHERE {cleaned.sql(dialect='mysql')}"

                optimizations.append(OptimizationApplied(
                    rule="tautology_removal",
                    description=(
                        "Removed tautological condition from WHERE clause."
                    ),
                    original_fragment=original_where,
                    optimized_fragment=optimized_where,
                ))

    @staticmethod
    def _is_tautology(condition: exp.Expression) -> bool:
        """Check if an expression is a known tautology (always true)."""
        # TRUE literal
        if isinstance(condition, exp.Boolean) and condition.this:
            return True

        # 1 = 1
        if isinstance(condition, exp.EQ):
            left = condition.left
            right = condition.right
            if (
                isinstance(left, exp.Literal)
                and isinstance(right, exp.Literal)
                and left.this == right.this
            ):
                return True

        # Bare literal 1
        if isinstance(condition, exp.Literal) and condition.this == "1":
            return True

        return False

    def _remove_tautology_from_and(
        self, condition: exp.And
    ) -> exp.Expression | None:
        """Remove tautological branches from an AND chain.

        Returns the cleaned condition, or None if both branches are tautologies.
        """
        left_is_taut = self._is_tautology(condition.left)
        right_is_taut = self._is_tautology(condition.right)

        if left_is_taut and right_is_taut:
            return None
        elif left_is_taut:
            return condition.right
        elif right_is_taut:
            return condition.left
        else:
            return condition

    # ------------------------------------------------------------------
    # Optimization Rule 4: Constant-false detection
    # ------------------------------------------------------------------

    def _try_detect_constant_false(
        self,
        ast: exp.Select,
        optimizations: list[OptimizationApplied],
    ) -> None:
        """Detect WHERE conditions that are always false (e.g. WHERE 1=0).

        This is an observation only — the query is not modified because
        the user may have intentionally written it (e.g., as a template).
        """
        where = ast.find(exp.Where)
        if where is None:
            return

        condition = where.this

        if self._is_constant_false(condition):
            optimizations.append(OptimizationApplied(
                rule="constant_false_detection",
                description=(
                    f"The WHERE condition "
                    f"'{condition.sql(dialect='mysql')}' is always false. "
                    f"This query will return zero rows."
                ),
                original_fragment=f"WHERE {condition.sql(dialect='mysql')}",
                optimized_fragment=None,
            ))

    @staticmethod
    def _is_constant_false(condition: exp.Expression) -> bool:
        """Check if an expression is always false."""
        # FALSE literal
        if isinstance(condition, exp.Boolean) and not condition.this:
            return True

        # 1 = 0
        if isinstance(condition, exp.EQ):
            left = condition.left
            right = condition.right
            if (
                isinstance(left, exp.Literal)
                and isinstance(right, exp.Literal)
                and left.this != right.this
            ):
                return True

        # Bare literal 0
        if isinstance(condition, exp.Literal) and condition.this == "0":
            return True

        return False

    # ------------------------------------------------------------------
    # Rule 5: Index opportunity recommendations
    # ------------------------------------------------------------------

    def _generate_index_recommendations(
        self,
        ast: exp.Select,
        table_lookup: dict[str, TableSchema],
        index_recommendations: list[str],
    ) -> None:
        """Generate index recommendations based on WHERE/JOIN columns.

        These are observations only — no indexes are created.
        """
        # Find WHERE columns that could benefit from an index
        where = ast.find(exp.Where)
        if where is None:
            return

        # Collect column references in the WHERE clause
        where_columns: list[tuple[str, str]] = []
        for col in where.find_all(exp.Column):
            col_name = col.name.lower()
            # Try to resolve table
            table_name = None
            if col.table:
                table_name = col.table.lower()
            else:
                # Resolve from single table
                for tbl_name, tbl_schema in table_lookup.items():
                    if any(c.name.lower() == col_name for c in tbl_schema.columns):
                        table_name = tbl_name
                        break

            if table_name and table_name in table_lookup:
                where_columns.append((table_name, col_name))

        # Check if these columns are already indexed
        for table_name, col_name in where_columns:
            tbl = table_lookup[table_name]
            # Check primary keys
            if col_name in {pk.lower() for pk in tbl.primary_keys}:
                continue
            # Check foreign keys (typically indexed)
            fk_cols = {fk.column.lower() for fk in tbl.foreign_keys}
            if col_name in fk_cols:
                continue
            # Check unique constraints
            unique_cols: set[str] = set()
            for uc in tbl.unique_constraints:
                unique_cols.update(c.lower() for c in uc.columns)
            if col_name in unique_cols:
                continue

            # Not indexed — recommend
            index_recommendations.append(
                f"Column '{col_name}' on table '{table_name}' is used in a "
                f"WHERE clause but has no apparent index. Consider adding an "
                f"index if this query runs frequently."
            )
