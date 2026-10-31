"""
Schema Parser service for parsing and validating user-provided temporary schemas.

Accepts schema definitions as:
  1. Structured UserProvidedSchema Pydantic objects
  2. Bulleted / Markdown text format (e.g., table name followed by '- col TYPE')
  3. Simplified DDL (CREATE TABLE table_name (...))

Security and Safety:
  - User-provided schema is METADATA ONLY, not executable SQL.
  - Semicolons, comment markers (-- , /*, #), and DDL/DML statements
    (DROP, DELETE, INSERT, UPDATE, ALTER, TRUNCATE, GRANT, REVOKE, CALL)
    are strictly rejected.
  - Table and column names must match standard SQL identifier regex: ^[a-zA-Z_][a-zA-Z0-9_]{0,63}$.
  - Data types are validated against allowed SQL types.
  - Parsed tables are tagged with source="user_provided".
"""

import re
import logging
from typing import Any

from app.models.schema_models import (
    ColumnSchema,
    ForeignKeySchema,
    TableSchema,
    UserColumnInput,
    UserForeignKeyInput,
    UserProvidedSchema,
    UserTableInput,
)

logger = logging.getLogger(__name__)

# Strict identifier regex (letters, numbers, underscore, starting with letter or underscore)
IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")

# Allowed SQL base types (case-insensitive)
ALLOWED_BASE_TYPES = {
    "INT", "INTEGER", "SMALLINT", "TINYINT", "MEDIUMINT", "BIGINT",
    "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL",
    "VARCHAR", "CHAR", "TEXT", "TINYTEXT", "MEDIUMTEXT", "LONGTEXT",
    "BOOLEAN", "BOOL", "BIT",
    "DATE", "DATETIME", "TIMESTAMP", "TIME", "YEAR",
    "JSON", "BLOB", "BINARY", "VARBINARY"
}

# Type pattern: BASE_TYPE optionally followed by (digits) or (digits, digits)
DATA_TYPE_REGEX = re.compile(
    r"^(?P<base>[a-zA-Z]+)(?:\(\s*\d+\s*(?:,\s*\d+\s*)?\))?$",
    re.IGNORECASE,
)

# Blocked dangerous SQL tokens
BLOCKED_DANGEROUS_TOKENS = {
    "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "EXEC", "EXECUTE", "UNION", "SELECT", "CALL"
}


class SchemaParsingError(Exception):
    """Raised when user-provided schema cannot be parsed or contains invalid metadata."""


class SchemaParser:
    """Parses and sanitizes user-provided schema into structured TableSchema objects."""

    def parse(self, user_schema: UserProvidedSchema | None, raw_text: str | None = None) -> list[TableSchema]:
        """Convert structured or text-based user schema into a list of TableSchema objects.

        Raises:
            SchemaParsingError: If any schema element is invalid or contains forbidden SQL.
        """
        tables: list[TableSchema] = []

        if user_schema and user_schema.tables:
            for table_input in user_schema.tables:
                tables.append(self._parse_user_table_input(table_input))

        if raw_text and raw_text.strip():
            text_tables = self.parse_text(raw_text)
            # Avoid duplicate table names if supplied in both
            existing_names = {t.name.lower() for t in tables}
            for t in text_tables:
                if t.name.lower() not in existing_names:
                    tables.append(t)
                    existing_names.add(t.name.lower())

        return tables

    def _parse_user_table_input(self, inp: UserTableInput) -> TableSchema:
        """Validate and convert a UserTableInput model into TableSchema."""
        self._validate_identifier(inp.name, "table name")

        columns: list[ColumnSchema] = []
        primary_keys: list[str] = list(inp.primary_keys)

        for col in inp.columns:
            self._validate_identifier(col.name, f"column name in table '{inp.name}'")
            clean_type = self._validate_data_type(col.type)
            columns.append(
                ColumnSchema(
                    name=col.name,
                    type=clean_type,
                    nullable=col.nullable,
                    default=col.default,
                    autoincrement=False,
                )
            )
            if col.primary_key and col.name not in primary_keys:
                primary_keys.append(col.name)

        foreign_keys: list[ForeignKeySchema] = []
        for fk in inp.foreign_keys:
            self._validate_identifier(fk.column, f"foreign key column in table '{inp.name}'")
            self._validate_identifier(fk.references_table, f"referenced table in table '{inp.name}'")
            self._validate_identifier(fk.references_column, f"referenced column in table '{inp.name}'")
            foreign_keys.append(
                ForeignKeySchema(
                    column=fk.column,
                    references_table=fk.references_table,
                    references_column=fk.references_column,
                    constraint_name=fk.constraint_name,
                )
            )

        return TableSchema(
            name=inp.name,
            columns=columns,
            primary_keys=primary_keys,
            foreign_keys=foreign_keys,
            source="user_provided",
        )

    def parse_text(self, text: str) -> list[TableSchema]:
        """Parse raw text (bullet points or DDL) into TableSchema objects.

        Example text:
            employees
            - id INT PRIMARY KEY
            - name VARCHAR(100)
            - salary DECIMAL(10,2)
            - department_id INT
            FOREIGN KEY (department_id) -> departments.id
        """
        # Security sanitization checks
        self._check_security_violations(text)

        cleaned_text = self._strip_markdown_fences(text.strip())

        # Check if text is a CREATE TABLE DDL
        if re.search(r"\bCREATE\s+TABLE\b", cleaned_text, re.IGNORECASE):
            return self._parse_ddl_text(cleaned_text)

        # Otherwise, parse line-by-line / bullet format
        return self._parse_bullet_text(cleaned_text)

    def _parse_bullet_text(self, text: str) -> list[TableSchema]:
        """Parse bulleted or line-based table schema specifications."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return []

        tables: list[TableSchema] = []
        current_table_name: str | None = None
        current_columns: list[ColumnSchema] = []
        current_pks: list[str] = []
        current_fks: list[ForeignKeySchema] = []

        def save_current_table():
            nonlocal current_table_name, current_columns, current_pks, current_fks
            if current_table_name:
                if not current_columns:
                    raise SchemaParsingError(f"Table '{current_table_name}' has no columns defined.")
                tables.append(
                    TableSchema(
                        name=current_table_name,
                        columns=current_columns,
                        primary_keys=current_pks,
                        foreign_keys=current_fks,
                        source="user_provided",
                    )
                )
            current_table_name = None
            current_columns = []
            current_pks = []
            current_fks = []

        for line in lines:
            # Check for header: "Table: employees" or "table employees"
            table_header_match = re.match(r"^(?:table\s*:?\s*)?([a-zA-Z_][a-zA-Z0-9_]*)\s*:?$", line, re.IGNORECASE)
            is_fk_line = re.search(r"\bFOREIGN\s+KEY\b|->|REFERENCES\b", line, re.IGNORECASE)

            # Check if line looks like a standalone table name
            if table_header_match and not line.startswith(("-", "*")) and not is_fk_line:
                raw_name = table_header_match.group(1)
                # Ensure it's not a column or keyword
                if raw_name.upper() not in ALLOWED_BASE_TYPES:
                    save_current_table()
                    self._validate_identifier(raw_name, "table name")
                    current_table_name = raw_name
                    continue

            # Check for FK line: e.g. "FOREIGN KEY (dept_id) -> departments.id" or "FOREIGN KEY (dept_id) REFERENCES departments(id)"
            if is_fk_line:
                fk = self._parse_fk_line(line)
                if fk:
                    current_fks.append(fk)
                    continue

            # Check for column line: e.g. "- id INT PRIMARY KEY" or "id INT"
            col_match = re.match(r"^[-*]?\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+([a-zA-Z]+(?:\(\s*\d+\s*(?:,\s*\d+\s*)?\))?)(.*)$", line, re.IGNORECASE)
            if col_match:
                col_name = col_match.group(1)
                raw_type = col_match.group(2)
                modifiers = col_match.group(3).strip().upper()

                if not current_table_name:
                    # If no table name was given yet, infer default or raise
                    current_table_name = "custom_table"

                self._validate_identifier(col_name, f"column name in '{current_table_name}'")
                clean_type = self._validate_data_type(raw_type)

                is_pk = "PRIMARY KEY" in modifiers or "PK" in modifiers.split()
                nullable = "NOT NULL" not in modifiers and not is_pk

                current_columns.append(
                    ColumnSchema(
                        name=col_name,
                        type=clean_type,
                        nullable=nullable,
                        default=None,
                        autoincrement=False,
                    )
                )
                if is_pk and col_name not in current_pks:
                    current_pks.append(col_name)

                # Check if inline FK in modifiers: e.g. "-> departments.id" or "REFERENCES departments(id)"
                inline_fk = self._parse_inline_fk(col_name, modifiers)
                if inline_fk:
                    current_fks.append(inline_fk)
                continue

            # If line doesn't match any pattern, raise descriptive error
            raise SchemaParsingError(f"Could not parse schema line: '{line}'. Expected column or table definition.")

        save_current_table()
        return tables

    def _parse_ddl_text(self, ddl: str) -> list[TableSchema]:
        """Parse simplified CREATE TABLE statement(s) respecting nested parentheses."""
        tables: list[TableSchema] = []
        matches = list(re.finditer(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
            ddl,
            re.IGNORECASE,
        ))
        if not matches:
            raise SchemaParsingError("Malformed CREATE TABLE statement. Expected: CREATE TABLE name (col1 TYPE, ...);")

        for match in matches:
            table_name = match.group(1)
            start_pos = match.end()
            depth = 1
            end_pos = -1
            for i in range(start_pos, len(ddl)):
                if ddl[i] == "(":
                    depth += 1
                elif ddl[i] == ")":
                    depth -= 1
                    if depth == 0:
                        end_pos = i
                        break

            if end_pos == -1 or depth != 0:
                raise SchemaParsingError(f"Mismatched parentheses in CREATE TABLE '{table_name}'.")

            body = ddl[start_pos:end_pos]
            self._validate_identifier(table_name, "table name")
            columns: list[ColumnSchema] = []
            primary_keys: list[str] = []
            foreign_keys: list[ForeignKeySchema] = []

            # Split columns by comma, respecting parentheses (e.g. DECIMAL(10,2))
            col_defs = self._split_by_comma_respecting_parens(body)
            for col_def in col_defs:
                col_def = col_def.strip()
                if not col_def:
                    continue

                # Check for table-level PRIMARY KEY (id, ...)
                pk_table_match = re.match(r"^PRIMARY\s+KEY\s*\((.*?)\)", col_def, re.IGNORECASE)
                if pk_table_match:
                    pks = [p.strip() for p in pk_table_match.group(1).split(",")]
                    for pk in pks:
                        self._validate_identifier(pk, "primary key column")
                        if pk not in primary_keys:
                            primary_keys.append(pk)
                    continue

                # Check for table-level FOREIGN KEY (col) REFERENCES ref_table(ref_col)
                fk = self._parse_fk_line(col_def)
                if fk:
                    foreign_keys.append(fk)
                    continue

                # Column definition
                col_match = re.match(
                    r"^([a-zA-Z_][a-zA-Z0-9_]*)\s+([a-zA-Z]+(?:\(\s*\d+\s*(?:,\s*\d+\s*)?\))?)(.*)$",
                    col_def,
                    re.IGNORECASE,
                )
                if not col_match:
                    raise SchemaParsingError(f"Invalid column definition: '{col_def}'")

                col_name = col_match.group(1)
                raw_type = col_match.group(2)
                modifiers = col_match.group(3).strip().upper()

                self._validate_identifier(col_name, "column name")
                clean_type = self._validate_data_type(raw_type)
                is_pk = "PRIMARY KEY" in modifiers
                nullable = "NOT NULL" not in modifiers and not is_pk

                columns.append(
                    ColumnSchema(
                        name=col_name,
                        type=clean_type,
                        nullable=nullable,
                        default=None,
                        autoincrement=False,
                    )
                )
                if is_pk and col_name not in primary_keys:
                    primary_keys.append(col_name)

                inline_fk = self._parse_inline_fk(col_name, modifiers)
                if inline_fk:
                    foreign_keys.append(inline_fk)

            if not columns:
                raise SchemaParsingError(f"Table '{table_name}' has no columns defined.")

            tables.append(
                TableSchema(
                    name=table_name,
                    columns=columns,
                    primary_keys=primary_keys,
                    foreign_keys=foreign_keys,
                    source="user_provided",
                )
            )

        return tables

    def _parse_fk_line(self, line: str) -> ForeignKeySchema | None:
        """Parse standalone or bulleted FK definition.

        Formats supported:
          FOREIGN KEY (dept_id) -> departments.id
          FOREIGN KEY (dept_id) REFERENCES departments(id)
          dept_id -> departments.id
        """
        pattern1 = re.search(
            r"(?:FOREIGN\s+KEY\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)\s*)?(?:REFERENCES\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)|->\s*([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*))",
            line,
            re.IGNORECASE,
        )
        if pattern1:
            local_col = pattern1.group(1)
            ref_table = pattern1.group(2) or pattern1.group(4)
            ref_col = pattern1.group(3) or pattern1.group(5)

            # If local_col was not in parentheses: e.g. "dept_id -> departments.id"
            if not local_col:
                left_match = re.match(r"^[-*]?\s*([a-zA-Z_][a-zA-Z0-9_]*)", line)
                if left_match:
                    local_col = left_match.group(1)

            if local_col and ref_table and ref_col:
                self._validate_identifier(local_col, "foreign key local column")
                self._validate_identifier(ref_table, "foreign key referenced table")
                self._validate_identifier(ref_col, "foreign key referenced column")
                return ForeignKeySchema(
                    column=local_col,
                    references_table=ref_table,
                    references_column=ref_col,
                    constraint_name=f"fk_{local_col}_{ref_table}",
                )
        return None

    def _parse_inline_fk(self, col_name: str, modifiers: str) -> ForeignKeySchema | None:
        """Parse inline FK references inside column modifiers."""
        match = re.search(
            r"(?:REFERENCES\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)|->\s*([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*))",
            modifiers,
            re.IGNORECASE,
        )
        if match:
            ref_table = match.group(1) or match.group(3)
            ref_col = match.group(2) or match.group(4)
            self._validate_identifier(ref_table, "referenced table")
            self._validate_identifier(ref_col, "referenced column")
            return ForeignKeySchema(
                column=col_name,
                references_table=ref_table,
                references_column=ref_col,
                constraint_name=f"fk_{col_name}_{ref_table}",
            )
        return None

    def _validate_identifier(self, identifier: str, field_name: str) -> None:
        """Validate an SQL identifier (table/column name)."""
        if not identifier or not IDENTIFIER_REGEX.match(identifier.strip()):
            raise SchemaParsingError(
                f"Invalid {field_name}: '{identifier}'. Identifiers must start with a letter or "
                f"underscore and contain only letters, numbers, and underscores (max 64 characters)."
            )
        if identifier.upper() in BLOCKED_DANGEROUS_TOKENS:
            raise SchemaParsingError(
                f"Forbidden keyword '{identifier}' cannot be used as a {field_name}."
            )

    def _validate_data_type(self, raw_type: str) -> str:
        """Validate data type string against allowed SQL types."""
        clean = raw_type.strip()
        match = DATA_TYPE_REGEX.match(clean)
        if not match:
            raise SchemaParsingError(
                f"Invalid or unsupported data type: '{raw_type}'. "
                f"Examples: INT, VARCHAR(100), DECIMAL(10,2)."
            )
        base = match.group("base").upper()
        if base not in ALLOWED_BASE_TYPES:
            raise SchemaParsingError(
                f"Unsupported SQL data type '{base}'. Allowed types include: "
                f"{', '.join(sorted(ALLOWED_BASE_TYPES)[:10])}..."
            )
        return clean.upper()

    def _check_security_violations(self, text: str) -> None:
        """Check for SQL injection attempts, comment tokens, and banned statements."""
        # Check for semicolons
        if ";" in text:
            # Allow semicolon only at the very end of statement
            stripped = text.strip()
            if stripped.count(";") > 1 or (not stripped.endswith(";") and ";" in stripped):
                raise SchemaParsingError(
                    "Semicolons are not allowed inside schema definitions (multi-statement injection detected)."
                )

        # Check for SQL comments
        if "--" in text or "/*" in text or "*/" in text:
            raise SchemaParsingError("SQL comment markers (-- or /* */) are not permitted in schema definitions.")

        # Check for banned statements / tokens
        upper_text = text.upper()
        for token in BLOCKED_DANGEROUS_TOKENS:
            # Check whole word match
            if re.search(r"\b" + re.escape(token) + r"\b", upper_text):
                # Only CREATE is allowed if it's CREATE TABLE
                if token == "CREATE":
                    if not re.search(r"\bCREATE\s+TABLE\b", upper_text):
                        raise SchemaParsingError(f"Forbidden statement keyword: '{token}'. Schema is metadata only.")
                else:
                    raise SchemaParsingError(f"Forbidden statement keyword: '{token}'. Schema is metadata only.")

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        """Remove markdown code blocks."""
        pattern = re.compile(r"^```(?:sql)?\s*\n?(.*?)\n?```\s*$", re.DOTALL | re.IGNORECASE)
        match = pattern.match(text)
        if match:
            return match.group(1).strip()
        return text

    @staticmethod
    def _split_by_comma_respecting_parens(s: str) -> list[str]:
        """Split a string by commas, but not inside parentheses."""
        parts = []
        current = []
        depth = 0
        for char in s:
            if char == "(":
                depth += 1
                current.append(char)
            elif char == ")":
                depth = max(0, depth - 1)
                current.append(char)
            elif char == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(char)
        if current:
            parts.append("".join(current).strip())
        return parts
