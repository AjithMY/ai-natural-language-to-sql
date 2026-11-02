"""
Prompt builder for Phase 3: NL-to-SQL generation.

Responsible for converting a DatabaseSchema object and a user question
into a well-structured prompt that instructs Gemini to produce a valid
MySQL SELECT query.

Design principles:
  - The schema context is always derived from the live database — nothing
    is hardcoded here (no table names, column names, or data types).
  - The prompt explicitly forbids data-modifying statements and Markdown
    code fences so that the returned text can be used as-is.
  - This module has NO dependency on the Gemini SDK, database connections,
    or HTTP layer.  It is a pure text-transformation utility.
"""

from app.models.schema_models import DatabaseSchema, ForeignKeySchema, TableSchema


class PromptBuilder:
    """Builds the prompt sent to Gemini for SQL generation.

    Usage::

        builder = PromptBuilder()
        prompt = builder.build_prompt(schema, question)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_prompt(self, schema: DatabaseSchema, question: str) -> str:
        """Return the full prompt string to send to Gemini.

        Args:
            schema: The live DatabaseSchema retrieved from SchemaManager.
            question: The user's natural-language question.

        Returns:
            A multi-line string ready to pass to the Gemini API.
        """
        schema_context = self._format_schema(schema)
        return self._assemble_prompt(schema_context, question)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _format_schema(self, schema: DatabaseSchema) -> str:
        """Convert a DatabaseSchema into a readable text representation.

        The output includes:
          - Database name
          - For each table: name, columns (with type + nullability),
            primary keys, foreign keys, and unique constraints.

        Args:
            schema: The database schema to format.

        Returns:
            A formatted string describing the complete schema.
        """
        lines: list[str] = [
            f"Database: {schema.database}",
            "",
        ]

        for table in schema.tables:
            lines.extend(self._format_table(table))

        return "\n".join(lines)

    def _format_table(self, table: TableSchema) -> list[str]:
        """Return lines describing a single table's schema."""
        lines: list[str] = [f"Table: {table.name}"]

        # --- Columns ---
        for col in table.columns:
            is_pk = col.name in table.primary_keys
            pk_marker = " [PRIMARY KEY]" if is_pk else ""
            nullable_marker = "" if col.nullable else " NOT NULL"
            ai_marker = " AUTO_INCREMENT" if col.autoincrement else ""
            lines.append(
                f"  - {col.name} {col.type}{nullable_marker}{ai_marker}{pk_marker}"
            )

        # --- Foreign keys ---
        for fk in table.foreign_keys:
            lines.append(
                f"  FOREIGN KEY ({fk.column}) -> {fk.references_table}.{fk.references_column}"
            )

        # --- Unique constraints ---
        for uc in table.unique_constraints:
            cols = ", ".join(uc.columns)
            lines.append(f"  UNIQUE ({cols})")

        lines.append("")  # blank line between tables
        return lines

    def _assemble_prompt(self, schema_context: str, question: str) -> str:
        """Combine the schema context and user question into the final prompt."""
        return (
            "You are an expert SQL query generation assistant.\n"
            "\n"
            "## Instructions\n"
            "\n"
            "- Generate a MySQL SELECT query that answers the user's question.\n"
            "- Use ONLY the tables and columns provided in the database schema below.\n"
            "- Do NOT invent tables or columns that are not in the schema.\n"
            "- Use foreign key relationships to JOIN tables when necessary.\n"
            "- SQL dialect: MySQL. Use MySQL-compatible syntax (e.g. LIMIT, not TOP).\n"
            "- Generate ONLY a SELECT query. Do NOT generate INSERT, UPDATE, DELETE,\n"
            "  DROP, ALTER, TRUNCATE, CREATE, or any data-modifying statement.\n"
            "- Return ONLY the raw SQL query string.\n"
            "- Do NOT wrap the SQL in Markdown code fences (``` or ```sql).\n"
            "- Do NOT include any explanation, commentary, or extra text.\n"
            "- When filtering on status or enum columns (such as payment_status), match the exact\n"
            "  status keyword requested in the question (e.g. use payment_status = 'Pending' when\n"
            "  asked for 'pending payments' or 'pending fee payments', rather than != 'Paid').\n"
            "- Only include partial payments or broader conditions if the user explicitly asks for\n"
            "  'outstanding balance', 'unpaid balance', or 'all unpaid fees'.\n"
            "- Select only the specific columns requested or implied by the question rather than\n"
            "  expanding to all columns with *.\n"
            "- End the query with a semicolon.\n"
            "\n"
            "## Database Schema\n"
            "\n"
            f"{schema_context}\n"
            "## User Question\n"
            "\n"
            f"{question}\n"
            "\n"
            "## SQL Query\n"
        )

