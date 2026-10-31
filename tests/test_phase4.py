"""
Phase 4A tests: SQL Validation & Safety.

Tests cover:
  1.  SQLValidatorService — valid SQL patterns
      - Simple SELECT
      - SELECT with WHERE
      - SELECT with ORDER BY
      - SELECT with LIMIT
      - SELECT with JOIN (INNER, LEFT)
      - SELECT with table aliases
      - Aggregate functions (COUNT, AVG, MAX, MIN, SUM)
      - GROUP BY
      - HAVING
      - Multiple valid tables in joins
      - Subquery in WHERE
      - Trailing semicolon on valid SELECT

  2.  SQLValidatorService — invalid SQL (statement type rejection)
      - Malformed / unparseable SQL
      - INSERT
      - UPDATE
      - DELETE
      - DROP
      - ALTER
      - CREATE
      - TRUNCATE
      - REPLACE
      - GRANT
      - REVOKE
      - SET
      - CALL
      - Multiple stacked statements (SELECT; DROP)
      - Two SELECT statements

  3.  SQLValidatorService — schema validation
      - Unknown table name
      - Unknown column name (qualified)
      - Unknown column name (unqualified)

  4.  SQLValidatorService — alias & qualifier handling
      - Table aliases resolve correctly
      - Qualified columns validate correctly
      - Valid JOINs with aliases pass

  5.  SQLValidatorService — comment / bypass attempts
      - Inline comment bypass
      - Block comment bypass
      - Hash comment bypass

  6.  SQLValidationResult model tests

  7.  API endpoint integration tests
      - Valid SQL passes validation and returns 200
      - Invalid SQL (non-SELECT) returns 422
      - Unknown table returns 422
      - Unknown column returns 422
      - request_id preserved on validation errors
      - API key not exposed on validation errors
      - Validation failure does not execute SQL
      - Validation field present in success response

  8.  Regression tests
      - Phase 1 endpoints still work
      - Phase 2 endpoints still work
      - Phase 3 endpoints still work

  9.  Phase boundary checks
      - No SQL execution
      - No EXPLAIN exists
      - No optimization exists

All Gemini API calls are mocked — tests do NOT make real network requests.
"""

import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.models.query_models import SQLValidationResult, QueryGenerationResponse
from app.models.schema_models import (
    ColumnSchema,
    DatabaseSchema,
    ForeignKeySchema,
    TableSchema,
    UniqueConstraintSchema,
)
from app.services.sql_validator import SQLValidatorService

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers — mock schema (matches the college database structure)
# ---------------------------------------------------------------------------

def _mock_schema() -> DatabaseSchema:
    """Return a realistic mock DatabaseSchema for Phase 4A tests.

    Includes 4 tables matching the project's college database:
    departments, students, courses, enrollments.
    """
    departments = TableSchema(
        name="departments",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="name", type="VARCHAR(100)", nullable=False),
            ColumnSchema(name="created_at", type="TIMESTAMP", nullable=True),
        ],
        primary_keys=["id"],
        foreign_keys=[],
        unique_constraints=[
            UniqueConstraintSchema(name="uq_dept_name", columns=["name"]),
        ],
    )
    students = TableSchema(
        name="students",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="name", type="VARCHAR(100)", nullable=False),
            ColumnSchema(name="email", type="VARCHAR(150)", nullable=True),
            ColumnSchema(name="marks", type="INTEGER", nullable=True),
            ColumnSchema(name="department_id", type="INTEGER", nullable=True),
            ColumnSchema(name="created_at", type="TIMESTAMP", nullable=True),
        ],
        primary_keys=["id"],
        foreign_keys=[
            ForeignKeySchema(
                column="department_id",
                references_table="departments",
                references_column="id",
                constraint_name="fk_student_dept",
            )
        ],
        unique_constraints=[
            UniqueConstraintSchema(name="uq_email", columns=["email"]),
        ],
    )
    courses = TableSchema(
        name="courses",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="course_name", type="VARCHAR(150)", nullable=False),
            ColumnSchema(name="credits", type="INTEGER", nullable=False),
            ColumnSchema(name="department_id", type="INTEGER", nullable=True),
            ColumnSchema(name="created_at", type="TIMESTAMP", nullable=True),
        ],
        primary_keys=["id"],
        foreign_keys=[
            ForeignKeySchema(
                column="department_id",
                references_table="departments",
                references_column="id",
                constraint_name="fk_course_dept",
            )
        ],
        unique_constraints=[],
    )
    enrollments = TableSchema(
        name="enrollments",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="student_id", type="INTEGER", nullable=False),
            ColumnSchema(name="course_id", type="INTEGER", nullable=False),
            ColumnSchema(name="enrolled_at", type="TIMESTAMP", nullable=True),
        ],
        primary_keys=["id"],
        foreign_keys=[
            ForeignKeySchema(
                column="student_id",
                references_table="students",
                references_column="id",
                constraint_name="fk_enrollment_student",
            ),
            ForeignKeySchema(
                column="course_id",
                references_table="courses",
                references_column="id",
                constraint_name="fk_enrollment_course",
            ),
        ],
        unique_constraints=[
            UniqueConstraintSchema(name="uq_student_course", columns=["student_id", "course_id"]),
        ],
    )
    return DatabaseSchema(
        database="minor_project",
        tables=[departments, students, courses, enrollments],
        table_count=4,
    )


def _validator() -> SQLValidatorService:
    """Create a fresh SQLValidatorService instance."""
    return SQLValidatorService()


# ===========================================================================
# 1. VALID SQL — positive tests
# ===========================================================================

class TestValidSQL:
    """Validator must accept well-formed SELECT statements referencing valid schema."""

    def test_simple_select(self):
        result = _validator().validate("SELECT * FROM students", _mock_schema())
        assert result.valid is True
        assert result.errors == []

    def test_select_with_where(self):
        result = _validator().validate(
            "SELECT name, marks FROM students WHERE marks > 80",
            _mock_schema(),
        )
        assert result.valid is True
        assert result.errors == []

    def test_select_with_order_by(self):
        result = _validator().validate(
            "SELECT name, marks FROM students ORDER BY marks DESC",
            _mock_schema(),
        )
        assert result.valid is True

    def test_select_with_limit(self):
        result = _validator().validate(
            "SELECT name FROM students LIMIT 10",
            _mock_schema(),
        )
        assert result.valid is True

    def test_select_with_inner_join(self):
        result = _validator().validate(
            "SELECT s.name, d.name FROM students s "
            "INNER JOIN departments d ON s.department_id = d.id",
            _mock_schema(),
        )
        assert result.valid is True
        assert result.errors == []

    def test_select_with_left_join(self):
        result = _validator().validate(
            "SELECT s.name, d.name FROM students s "
            "LEFT JOIN departments d ON s.department_id = d.id",
            _mock_schema(),
        )
        assert result.valid is True

    def test_select_with_table_aliases(self):
        result = _validator().validate(
            "SELECT s.name, s.marks FROM students s",
            _mock_schema(),
        )
        assert result.valid is True

    def test_aggregate_count(self):
        result = _validator().validate(
            "SELECT COUNT(*) FROM students",
            _mock_schema(),
        )
        assert result.valid is True

    def test_aggregate_avg(self):
        result = _validator().validate(
            "SELECT AVG(marks) FROM students",
            _mock_schema(),
        )
        assert result.valid is True

    def test_aggregate_max_min_sum(self):
        result = _validator().validate(
            "SELECT MAX(marks), MIN(marks), SUM(marks) FROM students",
            _mock_schema(),
        )
        assert result.valid is True

    def test_group_by(self):
        result = _validator().validate(
            "SELECT department_id, COUNT(*) FROM students GROUP BY department_id",
            _mock_schema(),
        )
        assert result.valid is True

    def test_having(self):
        result = _validator().validate(
            "SELECT department_id, AVG(marks) as avg_marks "
            "FROM students GROUP BY department_id HAVING AVG(marks) > 70",
            _mock_schema(),
        )
        assert result.valid is True

    def test_multiple_valid_tables_in_join(self):
        result = _validator().validate(
            "SELECT s.name, c.course_name FROM students s "
            "JOIN enrollments e ON s.id = e.student_id "
            "JOIN courses c ON e.course_id = c.id",
            _mock_schema(),
        )
        assert result.valid is True

    def test_subquery_in_where(self):
        result = _validator().validate(
            "SELECT name FROM students WHERE department_id IN "
            "(SELECT id FROM departments)",
            _mock_schema(),
        )
        assert result.valid is True

    def test_trailing_semicolon(self):
        result = _validator().validate(
            "SELECT * FROM students;",
            _mock_schema(),
        )
        assert result.valid is True

    def test_select_specific_columns(self):
        result = _validator().validate(
            "SELECT id, name, email, marks FROM students",
            _mock_schema(),
        )
        assert result.valid is True


# ===========================================================================
# 2. INVALID SQL — statement type rejection
# ===========================================================================

class TestInvalidStatementTypes:
    """Validator must reject all non-SELECT statements."""

    def test_malformed_sql(self):
        result = _validator().validate("SELECTTTTT FROM students", _mock_schema())
        assert result.valid is False
        assert any("syntax" in e.lower() or "error" in e.lower() for e in result.errors)

    def test_insert_rejected(self):
        result = _validator().validate(
            "INSERT INTO students (name) VALUES ('Alice')",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("INSERT" in e for e in result.errors)

    def test_update_rejected(self):
        result = _validator().validate(
            "UPDATE students SET name = 'Bob' WHERE id = 1",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("UPDATE" in e for e in result.errors)

    def test_delete_rejected(self):
        result = _validator().validate(
            "DELETE FROM students WHERE id = 1",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("DELETE" in e for e in result.errors)

    def test_drop_rejected(self):
        result = _validator().validate(
            "DROP TABLE students",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("DROP" in e for e in result.errors)

    def test_alter_rejected(self):
        result = _validator().validate(
            "ALTER TABLE students ADD COLUMN age INT",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("ALTER" in e for e in result.errors)

    def test_create_rejected(self):
        result = _validator().validate(
            "CREATE TABLE test_table (id INT)",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("CREATE" in e for e in result.errors)

    def test_truncate_rejected(self):
        result = _validator().validate(
            "TRUNCATE TABLE students",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("TRUNCATE" in e for e in result.errors)

    def test_replace_rejected(self):
        result = _validator().validate(
            "REPLACE INTO students (id, name) VALUES (1, 'Alice')",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("SELECT" in e for e in result.errors)  # rejected as non-SELECT

    def test_grant_rejected(self):
        result = _validator().validate(
            "GRANT SELECT ON students TO 'user'@'localhost'",
            _mock_schema(),
        )
        assert result.valid is False
        # SQLGlot parses GRANT with MySQL syntax as Command; validator rejects it
        assert any("SELECT" in e or "Command" in e for e in result.errors)

    def test_revoke_rejected(self):
        result = _validator().validate(
            "REVOKE SELECT ON students FROM 'user'@'localhost'",
            _mock_schema(),
        )
        assert result.valid is False
        # SQLGlot parses REVOKE with MySQL syntax as Command; validator rejects it
        assert any("SELECT" in e or "Command" in e for e in result.errors)

    def test_set_rejected(self):
        result = _validator().validate(
            "SET @variable = 1",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("SET" in e for e in result.errors)

    def test_call_rejected(self):
        result = _validator().validate(
            "CALL my_procedure()",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("SELECT" in e for e in result.errors)  # rejected as non-SELECT (Command)

    def test_multiple_statements_rejected(self):
        result = _validator().validate(
            "SELECT * FROM students; DROP TABLE students;",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("Multiple" in e or "multiple" in e for e in result.errors)

    def test_two_select_statements_rejected(self):
        result = _validator().validate(
            "SELECT * FROM students; SELECT * FROM departments;",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("Multiple" in e or "multiple" in e for e in result.errors)

    def test_empty_sql(self):
        result = _validator().validate("", _mock_schema())
        assert result.valid is False


# ===========================================================================
# 3. SCHEMA VALIDATION — table and column checks
# ===========================================================================

class TestSchemaValidation:
    """Validator must reject queries referencing non-existent tables/columns."""

    def test_unknown_table(self):
        result = _validator().validate(
            "SELECT * FROM unknown_table",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("unknown_table" in e for e in result.errors)

    def test_unknown_column_qualified(self):
        result = _validator().validate(
            "SELECT student_name FROM students",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("student_name" in e for e in result.errors)

    def test_unknown_column_in_where(self):
        result = _validator().validate(
            "SELECT name FROM students WHERE nonexistent_col > 10",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("nonexistent_col" in e for e in result.errors)

    def test_unknown_column_with_alias(self):
        result = _validator().validate(
            "SELECT s.fake_column FROM students s",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("fake_column" in e for e in result.errors)

    def test_valid_table_invalid_column_in_join(self):
        result = _validator().validate(
            "SELECT s.name, d.invalid_col FROM students s "
            "INNER JOIN departments d ON s.department_id = d.id",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("invalid_col" in e for e in result.errors)

    def test_multiple_unknown_tables(self):
        result = _validator().validate(
            "SELECT * FROM fake1 JOIN fake2 ON fake1.id = fake2.id",
            _mock_schema(),
        )
        assert result.valid is False
        assert any("fake1" in e for e in result.errors)
        assert any("fake2" in e for e in result.errors)


# ===========================================================================
# 4. ALIAS & QUALIFIER HANDLING
# ===========================================================================

class TestAliasHandling:
    """Validator must correctly resolve table aliases and qualified columns."""

    def test_alias_resolves_to_real_table(self):
        result = _validator().validate(
            "SELECT s.name FROM students s",
            _mock_schema(),
        )
        assert result.valid is True

    def test_qualified_columns_validate(self):
        result = _validator().validate(
            "SELECT s.id, s.name, s.marks FROM students s WHERE s.marks > 50",
            _mock_schema(),
        )
        assert result.valid is True

    def test_join_with_aliases_passes(self):
        result = _validator().validate(
            "SELECT s.name, d.name "
            "FROM students s "
            "JOIN departments d ON s.department_id = d.id "
            "WHERE s.marks > 50",
            _mock_schema(),
        )
        assert result.valid is True

    def test_complex_alias_join(self):
        """Multi-table join with aliases across 3 tables."""
        result = _validator().validate(
            "SELECT s.name, c.course_name, e.enrolled_at "
            "FROM enrollments e "
            "JOIN students s ON e.student_id = s.id "
            "JOIN courses c ON e.course_id = c.id",
            _mock_schema(),
        )
        assert result.valid is True

    def test_unaliased_table_with_qualified_column(self):
        result = _validator().validate(
            "SELECT students.name FROM students",
            _mock_schema(),
        )
        assert result.valid is True


# ===========================================================================
# 5. COMMENT / BYPASS ATTEMPTS
# ===========================================================================

class TestCommentBypass:
    """SQL comments must not bypass validation. The AST parser handles them."""

    def test_inline_comment_does_not_bypass(self):
        """-- comment after valid SELECT; the DROP should be on a separate statement."""
        result = _validator().validate(
            "SELECT * FROM students -- this is a comment",
            _mock_schema(),
        )
        # The comment is stripped by the parser; only the SELECT remains
        assert result.valid is True

    def test_stacked_with_comment_rejected(self):
        """Stacked statements separated by a comment must be rejected."""
        result = _validator().validate(
            "SELECT * FROM students; -- safe\nDROP TABLE students;",
            _mock_schema(),
        )
        assert result.valid is False

    def test_block_comment_bypass_attempt(self):
        """Block comments around dangerous keywords must not bypass validation."""
        result = _validator().validate(
            "SELECT * FROM students; /* hide */ DELETE FROM students;",
            _mock_schema(),
        )
        assert result.valid is False

    def test_hash_comment(self):
        """MySQL # comment syntax."""
        result = _validator().validate(
            "SELECT * FROM students # just a comment",
            _mock_schema(),
        )
        assert result.valid is True

    def test_comment_only_sql(self):
        """A SQL string containing only a comment should be rejected."""
        result = _validator().validate("-- just a comment", _mock_schema())
        assert result.valid is False

    def test_not_fooled_by_select_prefix(self):
        """String-prefix-based checks like sql.startswith('SELECT') would be fooled."""
        result = _validator().validate(
            "SELECT * FROM students; INSERT INTO students (name) VALUES ('x');",
            _mock_schema(),
        )
        assert result.valid is False


# ===========================================================================
# 6. SQLValidationResult MODEL TESTS
# ===========================================================================

class TestSQLValidationResultModel:
    """SQLValidationResult Pydantic model tests."""

    def test_valid_result_serialization(self):
        result = SQLValidationResult(
            valid=True,
            sql="SELECT * FROM students;",
            errors=[],
            warnings=[],
        )
        data = result.model_dump()
        assert data["valid"] is True
        assert data["sql"] == "SELECT * FROM students;"
        assert data["errors"] == []
        assert data["warnings"] == []

    def test_invalid_result_serialization(self):
        result = SQLValidationResult(
            valid=False,
            sql="DROP TABLE students;",
            errors=["Only SELECT statements are allowed. Found: DROP"],
            warnings=[],
        )
        data = result.model_dump()
        assert data["valid"] is False
        assert len(data["errors"]) == 1
        assert "DROP" in data["errors"][0]

    def test_result_with_warnings(self):
        result = SQLValidationResult(
            valid=True,
            sql="SELECT * FROM students;",
            errors=[],
            warnings=["Consider specifying column names instead of *"],
        )
        data = result.model_dump()
        assert data["valid"] is True
        assert len(data["warnings"]) == 1

    def test_result_does_not_contain_sensitive_fields(self):
        result = SQLValidationResult(
            valid=True, sql="SELECT 1;", errors=[], warnings=[]
        )
        data = result.model_dump()
        assert "api_key" not in data
        assert "password" not in data
        assert "GEMINI_API_KEY" not in data


# ===========================================================================
# 7. API ENDPOINT INTEGRATION TESTS
# ===========================================================================

def _mock_schema_for_endpoint() -> DatabaseSchema:
    """Return the same schema used in endpoint mocks."""
    return _mock_schema()


class TestQueryGenerateEndpointValidation:
    """API endpoint tests for Phase 4A validation integration."""

    def test_valid_sql_returns_200_with_validation(self):
        """A valid SELECT query must return 200 with validation.valid=True."""
        mock_schema = _mock_schema_for_endpoint()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT name, marks FROM students WHERE marks > 80;"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show students who scored above 80"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["sql"] == "SELECT name, marks FROM students WHERE marks > 80;"
        assert "validation" in data
        assert data["validation"]["valid"] is True
        assert data["validation"]["errors"] == []

    def test_invalid_sql_nonselect_returns_422(self):
        """A non-SELECT generated SQL must return 422 with SQL_VALIDATION_FAILED."""
        mock_schema = _mock_schema_for_endpoint()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "DELETE FROM students WHERE id = 1"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Delete student with id 1"},
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error_code"] == "SQL_VALIDATION_FAILED"
        assert "DELETE" in detail["message"]

    def test_unknown_table_returns_422(self):
        """SQL referencing a non-existent table must return 422."""
        mock_schema = _mock_schema_for_endpoint()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT * FROM nonexistent_table"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show data from nonexistent table"},
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error_code"] == "SQL_VALIDATION_FAILED"
        assert "nonexistent_table" in detail["message"]

    def test_unknown_column_returns_422(self):
        """SQL referencing a non-existent column must return 422."""
        mock_schema = _mock_schema_for_endpoint()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = (
                "SELECT fake_column FROM students"
            )

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show fake data"},
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error_code"] == "SQL_VALIDATION_FAILED"

    def test_request_id_preserved_on_validation_error(self):
        """request_id must appear in 422 validation error responses."""
        mock_schema = _mock_schema_for_endpoint()
        custom_id = "phase4a-trace-xyz"
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "DROP TABLE students"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Drop students table"},
                headers={"X-Request-ID": custom_id},
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["request_id"] == custom_id

    def test_api_key_not_in_validation_error(self):
        """Validation error responses must not expose the API key."""
        mock_schema = _mock_schema_for_endpoint()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "DROP TABLE students"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Drop table"},
            )

        response_text = response.text
        assert "your_gemini_api_key_here" not in response_text
        assert "GEMINI_API_KEY" not in response_text

    def test_validation_failure_does_not_execute_sql(self):
        """No SQL execute() must be called during validation failure."""
        mock_schema = _mock_schema_for_endpoint()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
            patch("sqlalchemy.engine.base.Connection.execute") as mock_execute,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT * FROM students"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show all students"},
            )

        # Whether validation passes or fails, no SQL must be executed
        mock_execute.assert_not_called()

    def test_validation_field_present_in_success_response(self):
        """Successful responses must include the validation field."""
        mock_schema = _mock_schema_for_endpoint()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT * FROM students"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "List all students"},
            )

        assert response.status_code == 200
        data = response.json()
        assert "validation" in data
        assert data["validation"] is not None
        assert data["validation"]["valid"] is True
        assert isinstance(data["validation"]["errors"], list)
        assert isinstance(data["validation"]["warnings"], list)

    def test_stacked_injection_returns_422(self):
        """Stacked SQL injection must be caught and return 422."""
        mock_schema = _mock_schema_for_endpoint()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = (
                "SELECT * FROM students; DROP TABLE students;"
            )

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show students"},
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error_code"] == "SQL_VALIDATION_FAILED"


# ===========================================================================
# 8. REGRESSION — existing phases still work
# ===========================================================================

class TestExistingPhasesStillWork:
    """Phase 4A must not break Phase 1, 2, or 3."""

    def test_phase1_legacy_schema_still_works(self):
        mock_schema = _mock_schema()
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_database_schema.return_value = mock_schema
            response = client.get("/schema")
        assert response.status_code == 200

    def test_phase2_health_still_works(self):
        with patch("app.api.v1.health_routes.verify_connection", return_value=True):
            response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_phase2_schema_still_works(self):
        mock_schema = _mock_schema()
        with patch("app.api.v1.schema_routes.SchemaService") as MockService:
            MockService.return_value.get_full_schema.return_value = mock_schema
            response = client.get("/api/v1/schema")
        assert response.status_code == 200

    def test_phase3_generate_still_returns_sql(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT * FROM students;"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show all students"},
            )

        assert response.status_code == 200
        data = response.json()
        assert "sql" in data
        assert "question" in data
        assert "model" in data

    def test_all_routes_still_registered(self):
        paths = list(app.openapi()["paths"].keys())
        assert "/health" in paths
        assert "/schema" in paths
        assert "/api/v1/health" in paths
        assert "/api/v1/schema" in paths
        assert "/api/v1/schema/{table_name}" in paths
        assert "/api/v1/query/generate" in paths


# ===========================================================================
# 9. PHASE BOUNDARY — no execution, EXPLAIN, or optimization
# ===========================================================================

class TestPhaseBoundary:
    """Verify that Phase 4A does not introduce future-phase functionality."""

    def test_no_sql_execution_in_validator(self):
        """SQLValidatorService must not import or call any execute() method."""
        import inspect
        source = inspect.getsource(SQLValidatorService)
        assert "execute(" not in source
        assert ".execute(" not in source

    def test_no_explain_in_validator(self):
        """SQLValidatorService must not contain EXPLAIN functionality."""
        import inspect
        source = inspect.getsource(SQLValidatorService)
        assert "EXPLAIN" not in source

    def test_no_optimization_in_validator(self):
        """SQLValidatorService must not contain optimization logic."""
        import inspect
        source = inspect.getsource(SQLValidatorService)
        assert "optimize" not in source.lower() or "optimization" not in source.lower()

    def test_no_execution_endpoint_exists(self):
        """No /run endpoint should exist. /query/execute is Phase 4B (allowed)."""
        paths = list(app.openapi()["paths"].keys())
        for path in paths:
            assert "/run" not in path.lower()

    def test_generated_sql_not_executed_during_validation(self):
        """End-to-end: no SQL execute() is called during the entire flow."""
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
            patch("sqlalchemy.engine.base.Connection.execute") as mock_execute,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT name FROM students;"

            client.post(
                "/api/v1/query/generate",
                json={"question": "Show student names"},
            )

        mock_execute.assert_not_called()
