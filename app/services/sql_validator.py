"""
SQL Validator service for Phase 4A: SQL Validation & Safety.

Responsibilities:
  - Parse generated SQL using SQLGlot's AST parser (MySQL dialect).
  - Verify the SQL is a single SELECT statement.
  - Reject all non-SELECT statements (INSERT, UPDATE, DELETE, DROP, ALTER,
    CREATE, TRUNCATE, REPLACE, GRANT, REVOKE, SET, CALL, etc.).
  - Reject multiple / stacked SQL statements.
  - Validate that all referenced tables exist in the live database schema.
  - Validate that all referenced columns exist in the resolved tables
    (handling table aliases, qualified columns, and unqualified columns).
  - Return a structured SQLValidationResult with errors and warnings.

This service intentionally does NOT:
  - Execute any SQL.
  - Connect to the database at runtime (it receives schema as input).
  - Perform EXPLAIN or query-plan analysis.
  - Perform query optimization.

Security notes:
  - Gemini-generated SQL is treated as UNTRUSTED input.
  - All error messages are safe to surface to API clients — no credentials,
    stack traces, or internal implementation details are exposed.
  - SQL comments are handled by the AST parser, so comment-based injection
    attempts (e.g., ``SELECT * FROM students; -- DROP TABLE``) are caught
    at the multi-statement check level.
"""

import logging
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.models.query_models import SQLValidationResult
from app.models.schema_models import DatabaseSchema

logger = logging.getLogger(__name__)


class SQLValidatorService:
    """Validates generated SQL against safety rules and the live database schema.

    Usage::

        validator = SQLValidatorService()
        result = validator.validate(
            sql="SELECT name FROM students;",
            schema=database_schema,
        )
        if not result.valid:
            # handle errors in result.errors
    """

    # Statement types that are explicitly rejected.
    # SQLGlot parses these into known expression types.
    _BLOCKED_TYPES: set[type] = {
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Drop,
        exp.Alter,
        exp.Create,
        exp.TruncateTable,
        exp.Grant,
        exp.Revoke,
        exp.Set,
        exp.Command,      # Catches CALL, REPLACE, and other unsupported statements
        exp.Transaction,
        exp.Commit,
        exp.Rollback,
        exp.Use,
    }

    # Friendly names for blocked statement types (for error messages)
    _TYPE_NAMES: dict[type, str] = {
        exp.Insert: "INSERT",
        exp.Update: "UPDATE",
        exp.Delete: "DELETE",
        exp.Drop: "DROP",
        exp.Alter: "ALTER",
        exp.Create: "CREATE",
        exp.TruncateTable: "TRUNCATE",
        exp.Grant: "GRANT",
        exp.Revoke: "REVOKE",
        exp.Set: "SET",
        exp.Command: "Command",
        exp.Transaction: "TRANSACTION",
        exp.Commit: "COMMIT",
        exp.Rollback: "ROLLBACK",
        exp.Use: "USE",
    }

    def validate(self, sql: str, schema: DatabaseSchema) -> SQLValidationResult:
        """Validate a generated SQL string against safety rules and schema.

        Args:
            sql: The SQL string to validate (from Gemini, treated as untrusted).
            schema: The live database schema to validate table/column references.

        Returns:
            A SQLValidationResult indicating whether the SQL is valid, with
            any errors and warnings.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # --- Step 1: Parse the SQL ---
        statements = self._parse_sql(sql, errors)
        if errors:
            return SQLValidationResult(valid=False, sql=sql, errors=errors, warnings=warnings)

        # --- Step 2: Multiple statement check ---
        if len(statements) == 0:
            errors.append("Empty SQL statement.")
            return SQLValidationResult(valid=False, sql=sql, errors=errors, warnings=warnings)

        if len(statements) > 1:
            errors.append(
                "Multiple SQL statements are not allowed. "
                "Only a single SELECT statement is accepted."
            )
            return SQLValidationResult(valid=False, sql=sql, errors=errors, warnings=warnings)

        statement = statements[0]

        # --- Step 3: Statement type check ---
        if not self._is_select_statement(statement, errors):
            return SQLValidationResult(valid=False, sql=sql, errors=errors, warnings=warnings)

        # --- Step 4: Check for dangerous subexpressions (DDL/DML in subqueries) ---
        self._check_dangerous_subexpressions(statement, errors)
        if errors:
            return SQLValidationResult(valid=False, sql=sql, errors=errors, warnings=warnings)

        # --- Step 5: Table validation ---
        schema_tables = self._build_schema_lookup(schema)
        alias_map = self._extract_table_aliases(statement)
        referenced_tables = self._validate_tables(statement, schema_tables, errors)
        if errors:
            return SQLValidationResult(valid=False, sql=sql, errors=errors, warnings=warnings)

        # --- Step 6: Column validation ---
        self._validate_columns(statement, schema_tables, alias_map, referenced_tables, errors, warnings)

        is_valid = len(errors) == 0
        return SQLValidationResult(valid=is_valid, sql=sql, errors=errors, warnings=warnings)

    # ------------------------------------------------------------------
    # Step 1: SQL Parsing
    # ------------------------------------------------------------------

    def _parse_sql(self, sql: str, errors: list[str]) -> list[Any]:
        """Parse SQL using SQLGlot with MySQL dialect.

        Returns a list of parsed AST nodes. On parse failure, appends
        an error and returns an empty list.
        """
        try:
            statements = sqlglot.parse(
                sql,
                dialect="mysql",
                error_level=sqlglot.ErrorLevel.RAISE,
            )
            # Filter out None entries (empty statements from trailing semicolons)
            return [s for s in statements if s is not None]
        except ParseError as exc:
            safe_message = str(exc)
            # Ensure we don't leak anything unexpected — truncate long messages
            if len(safe_message) > 200:
                safe_message = safe_message[:200] + "..."
            errors.append(f"SQL syntax error: {safe_message}")
            logger.debug("SQL parse error: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Step 3: Statement type validation
    # ------------------------------------------------------------------

    def _is_select_statement(self, statement: Any, errors: list[str]) -> bool:
        """Check that the statement is a SELECT (or a UNION of SELECTs).

        Returns True if the statement is allowed, False otherwise.
        """
        if isinstance(statement, exp.Select):
            return True

        # UNION / INTERSECT / EXCEPT are compound SELECTs — allowed
        if isinstance(statement, exp.Union):
            return True

        # Check if it's a blocked type with a friendly name
        for blocked_type, type_name in self._TYPE_NAMES.items():
            if isinstance(statement, blocked_type):
                errors.append(
                    f"Only SELECT statements are allowed. "
                    f"Found: {type_name}"
                )
                return False

        # Any other unknown type
        errors.append(
            f"Only SELECT statements are allowed. "
            f"Found: {type(statement).__name__}"
        )
        return False

    # ------------------------------------------------------------------
    # Step 4: Dangerous subexpressions
    # ------------------------------------------------------------------

    def _check_dangerous_subexpressions(self, statement: Any, errors: list[str]) -> None:
        """Walk the AST to detect DDL/DML nested in subqueries or CTEs."""
        for node in statement.walk():
            if isinstance(node, tuple(self._BLOCKED_TYPES)):
                type_name = self._TYPE_NAMES.get(type(node), type(node).__name__)
                errors.append(
                    f"Dangerous nested statement detected: {type_name}. "
                    f"Only SELECT statements are allowed."
                )

    # ------------------------------------------------------------------
    # Step 5: Table validation
    # ------------------------------------------------------------------

    def _build_schema_lookup(self, schema: DatabaseSchema) -> dict[str, list[str]]:
        """Build a lookup: lowercase table name → list of lowercase column names."""
        lookup: dict[str, list[str]] = {}
        for table in schema.tables:
            lookup[table.name.lower()] = [col.name.lower() for col in table.columns]
        return lookup

    def _extract_table_aliases(self, statement: Any) -> dict[str, str]:
        """Build a map: lowercase alias → lowercase real table name.

        Also includes identity entries (table_name → table_name) for
        tables referenced without aliases.
        """
        alias_map: dict[str, str] = {}
        for table_node in statement.find_all(exp.Table):
            table_name = table_node.name.lower()
            alias = table_node.alias
            if alias:
                alias_map[alias.lower()] = table_name
            # Always map table name to itself
            alias_map[table_name] = table_name
        return alias_map

    def _validate_tables(
        self,
        statement: Any,
        schema_tables: dict[str, list[str]],
        errors: list[str],
    ) -> set[str]:
        """Validate that all referenced tables exist in the schema.

        Returns the set of valid lowercase table names referenced.
        """
        referenced: set[str] = set()
        for table_node in statement.find_all(exp.Table):
            table_name = table_node.name.lower()
            if table_name not in schema_tables:
                errors.append(
                    f"Table '{table_node.name}' does not exist in the database schema."
                )
            else:
                referenced.add(table_name)
        return referenced

    # ------------------------------------------------------------------
    # Step 6: Column validation
    # ------------------------------------------------------------------

    def _validate_columns(
        self,
        statement: Any,
        schema_tables: dict[str, list[str]],
        alias_map: dict[str, str],
        referenced_tables: set[str],
        errors: list[str],
        warnings: list[str],
    ) -> None:
        """Validate that referenced columns exist in the resolved tables.

        Handles:
          - Qualified columns (alias.col or table.col)
          - Unqualified columns (col) — checked against all referenced tables
          - Skips star (*) expressions
        """
        for col_node in statement.find_all(exp.Column):
            col_name = col_node.name.lower()
            table_ref = col_node.table  # '' if unqualified

            if col_name == "*":
                continue  # Star expressions are valid

            if table_ref:
                # Qualified column: resolve alias → real table → check column
                real_table = alias_map.get(table_ref.lower())
                if real_table is None:
                    # The table/alias reference is unknown — this would
                    # already be caught by table validation, but just in case:
                    errors.append(
                        f"Column '{col_node.name}' references unknown table or alias '{table_ref}'."
                    )
                    continue

                if real_table not in schema_tables:
                    # Table doesn't exist in schema (already reported)
                    continue

                table_columns = schema_tables[real_table]
                if col_name not in table_columns:
                    errors.append(
                        f"Column '{col_node.name}' does not exist in table '{real_table}'."
                    )
            else:
                # Unqualified column: check if it exists in any referenced table
                if not referenced_tables:
                    # No valid tables to check against
                    continue

                found = any(
                    col_name in schema_tables.get(t, [])
                    for t in referenced_tables
                )
                if not found:
                    errors.append(
                        f"Column '{col_node.name}' does not exist in any referenced table."
                    )
