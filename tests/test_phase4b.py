"""
Phase 4B tests: Safe SQL Execution.

Tests cover:
  1.  Valid execution — mocked DB responses for various SELECT patterns
  2.  Safety — non-SELECT statements are rejected before execution
  3.  Critical — prove invalid SQL never reaches execute()
  4.  Result structure — columns, rows, row_count, NULL, truncation
  5.  Error handling — DB failures, safe messages, request_id, no API key
  6.  Configuration — SQL_MAX_ROWS, SQL_QUERY_TIMEOUT_SECONDS
  7.  Regression — Phases 1–4A still work
  8.  Phase boundary — no EXPLAIN, no optimization

All database calls are mocked — tests do NOT require a live MySQL connection.
"""

import pytest
from datetime import datetime, date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.core.config import settings
from app.main import app
from app.models.query_models import (
    QueryExecutionRequest,
    QueryExecutionResponse,
    SQLValidationResult,
)
from app.models.schema_models import (
    ColumnSchema,
    DatabaseSchema,
    ForeignKeySchema,
    TableSchema,
    UniqueConstraintSchema,
)
from app.services.sql_execution_service import SQLExecutionService, SQLExecutionError
from app.services.sql_validator import SQLValidatorService

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_schema() -> DatabaseSchema:
    """Return the same 4-table schema used across the project tests."""
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


def _mock_db_result(columns: list[str], rows: list[tuple]) -> MagicMock:
    """Create a mock SQLAlchemy CursorResult."""
    mock_result = MagicMock()
    mock_result.keys.return_value = columns
    mock_result.fetchmany.return_value = rows
    return mock_result


def _patch_execution(sql_return_columns, sql_return_rows):
    """Return context managers that mock schema + DB connection for endpoint tests."""
    mock_schema = _mock_schema()
    mock_result = _mock_db_result(sql_return_columns, sql_return_rows)

    schema_patch = patch(
        "app.services.sql_execution_service.SchemaService"
    )
    engine_patch = patch(
        "app.services.sql_execution_service.get_engine"
    )

    return schema_patch, engine_patch, mock_schema, mock_result


# ===========================================================================
# 1. REQUEST MODEL TESTS
# ===========================================================================

class TestQueryExecutionRequest:
    """Request model validation."""

    def test_accepts_valid_sql(self):
        req = QueryExecutionRequest(sql="SELECT * FROM students")
        assert req.sql == "SELECT * FROM students"

    def test_empty_sql_rejected(self):
        with pytest.raises(ValidationError):
            QueryExecutionRequest(sql="")

    def test_whitespace_sql_rejected(self):
        with pytest.raises(ValidationError):
            QueryExecutionRequest(sql="   ")

    def test_sql_is_stripped(self):
        req = QueryExecutionRequest(sql="  SELECT * FROM students  ")
        assert req.sql == "SELECT * FROM students"


# ===========================================================================
# 2. RESPONSE MODEL TESTS
# ===========================================================================

class TestQueryExecutionResponse:
    """Response model serialization."""

    def test_response_serializes_correctly(self):
        resp = QueryExecutionResponse(
            sql="SELECT name FROM students",
            columns=["name"],
            rows=[["Alice"], ["Bob"]],
            row_count=2,
            truncated=False,
            max_rows=1000,
            validation=SQLValidationResult(
                valid=True, sql="SELECT name FROM students", errors=[], warnings=[]
            ),
            request_id="req-123",
        )
        data = resp.model_dump()
        assert data["sql"] == "SELECT name FROM students"
        assert data["columns"] == ["name"]
        assert data["rows"] == [["Alice"], ["Bob"]]
        assert data["row_count"] == 2
        assert data["truncated"] is False
        assert data["max_rows"] == 1000
        assert data["validation"]["valid"] is True

    def test_response_with_null_values(self):
        resp = QueryExecutionResponse(
            sql="SELECT name, marks FROM students",
            columns=["name", "marks"],
            rows=[["Alice", 95], ["Bob", None]],
            row_count=2,
            truncated=False,
            max_rows=1000,
            validation=SQLValidationResult(
                valid=True, sql="SELECT name, marks FROM students", errors=[], warnings=[]
            ),
        )
        data = resp.model_dump()
        assert data["rows"][1][1] is None

    def test_truncated_flag(self):
        resp = QueryExecutionResponse(
            sql="SELECT * FROM students",
            columns=["id"],
            rows=[[i] for i in range(5)],
            row_count=5,
            truncated=True,
            max_rows=5,
            validation=SQLValidationResult(
                valid=True, sql="SELECT * FROM students", errors=[], warnings=[]
            ),
        )
        assert resp.truncated is True

    def test_response_does_not_contain_sensitive_fields(self):
        resp = QueryExecutionResponse(
            sql="SELECT 1",
            columns=["1"],
            rows=[[1]],
            row_count=1,
            truncated=False,
            max_rows=1000,
            validation=SQLValidationResult(
                valid=True, sql="SELECT 1", errors=[], warnings=[]
            ),
        )
        data = resp.model_dump()
        assert "api_key" not in data
        assert "password" not in data
        assert "GEMINI_API_KEY" not in data


# ===========================================================================
# 3. VALUE SERIALIZATION TESTS
# ===========================================================================

class TestValueSerialization:
    """Test SQLExecutionService._serialize_value for various MySQL types."""

    def test_none_value(self):
        assert SQLExecutionService._serialize_value(None) is None

    def test_int_value(self):
        assert SQLExecutionService._serialize_value(42) == 42

    def test_float_value(self):
        assert SQLExecutionService._serialize_value(3.14) == 3.14

    def test_str_value(self):
        assert SQLExecutionService._serialize_value("hello") == "hello"

    def test_bool_value(self):
        assert SQLExecutionService._serialize_value(True) is True

    def test_decimal_value(self):
        result = SQLExecutionService._serialize_value(Decimal("99.99"))
        assert result == 99.99
        assert isinstance(result, float)

    def test_datetime_value(self):
        dt = datetime(2025, 6, 15, 10, 30, 0)
        result = SQLExecutionService._serialize_value(dt)
        assert result == "2025-06-15T10:30:00"

    def test_date_value(self):
        d = date(2025, 6, 15)
        result = SQLExecutionService._serialize_value(d)
        assert result == "2025-06-15"

    def test_timedelta_value(self):
        td = timedelta(hours=2, minutes=30)
        result = SQLExecutionService._serialize_value(td)
        assert result == 9000.0  # 2.5 hours in seconds

    def test_bytes_value(self):
        result = SQLExecutionService._serialize_value(b"\xde\xad")
        assert result == "dead"

    def test_unknown_type_fallback(self):
        """Unknown types should be converted to string."""
        result = SQLExecutionService._serialize_value(complex(1, 2))
        assert isinstance(result, str)


# ===========================================================================
# 4. EXECUTION SERVICE UNIT TESTS
# ===========================================================================

class TestSQLExecutionServiceUnit:
    """Unit tests for SQLExecutionService with mocked DB."""

    def test_valid_select_executes(self):
        """A valid SELECT should be executed and return results."""
        mock_schema = _mock_schema()
        mock_result = _mock_db_result(["name", "marks"], [("Alice", 95), ("Bob", 72)])

        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result

            service = SQLExecutionService()
            result = service.execute("SELECT name, marks FROM students")

        assert result["validation"].valid is True
        assert result["columns"] == ["name", "marks"]
        assert result["row_count"] == 2
        assert result["truncated"] is False

    def test_invalid_sql_not_executed(self):
        """Invalid SQL must NOT reach database execution."""
        mock_schema = _mock_schema()

        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            service = SQLExecutionService()
            result = service.execute("DELETE FROM students")

        assert result["validation"].valid is False
        assert result["row_count"] == 0
        assert result["columns"] == []
        assert result["rows"] == []
        # Engine connect should NOT have been called
        mock_engine.return_value.connect.assert_not_called()

    def test_unknown_table_not_executed(self):
        """SQL referencing unknown table must NOT be executed."""
        mock_schema = _mock_schema()

        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            service = SQLExecutionService()
            result = service.execute("SELECT * FROM nonexistent_table")

        assert result["validation"].valid is False
        mock_engine.return_value.connect.assert_not_called()

    def test_truncation_detection(self):
        """When rows exceed max_rows, truncated should be True."""
        mock_schema = _mock_schema()
        # Return max_rows+1 rows to trigger truncation
        big_rows = [(i,) for i in range(4)]  # 4 rows: exceeds limit of 3

        mock_result = _mock_db_result(["id"], big_rows)

        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
            patch.object(settings, "SQL_MAX_ROWS", 3),
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result

            service = SQLExecutionService()
            result = service.execute("SELECT id FROM students")

        assert result["truncated"] is True
        assert result["row_count"] == 3  # truncated to max_rows

    def test_execution_error_raises_safe_exception(self):
        """Database execution failure should raise SQLExecutionError."""
        mock_schema = _mock_schema()

        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            # First execute (SET timeout) succeeds, second (query) fails
            mock_conn.execute.side_effect = [
                None,  # SET SESSION succeeds
                OperationalError("statement", {}, Exception("connection lost")),
            ]

            service = SQLExecutionService()
            with pytest.raises(SQLExecutionError):
                service.execute("SELECT * FROM students")


# ===========================================================================
# 5. API ENDPOINT TESTS — VALID EXECUTION
# ===========================================================================

class TestExecuteEndpointValid:
    """Endpoint tests with mocked valid execution."""

    def _make_request(self, sql, mock_columns, mock_rows, headers=None):
        """Helper: post to /api/v1/query/execute with mocked execution."""
        mock_schema = _mock_schema()
        mock_result = _mock_db_result(mock_columns, mock_rows)

        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result

            return client.post(
                "/api/v1/query/execute",
                json={"sql": sql},
                headers=headers or {},
            )

    def test_simple_select_returns_200(self):
        response = self._make_request(
            "SELECT * FROM students",
            ["id", "name", "marks"],
            [(1, "Alice", 95), (2, "Bob", 72)],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["columns"] == ["id", "name", "marks"]
        assert data["row_count"] == 2
        assert data["truncated"] is False

    def test_select_with_where(self):
        response = self._make_request(
            "SELECT name, marks FROM students WHERE marks > 80",
            ["name", "marks"],
            [("Alice", 95)],
        )
        assert response.status_code == 200
        assert response.json()["row_count"] == 1

    def test_select_with_order_by(self):
        response = self._make_request(
            "SELECT name FROM students ORDER BY marks DESC",
            ["name"],
            [("Alice",), ("Bob",)],
        )
        assert response.status_code == 200

    def test_select_with_limit(self):
        response = self._make_request(
            "SELECT name FROM students LIMIT 5",
            ["name"],
            [("Alice",)],
        )
        assert response.status_code == 200

    def test_select_with_join(self):
        response = self._make_request(
            "SELECT s.name, d.name FROM students s "
            "INNER JOIN departments d ON s.department_id = d.id",
            ["name", "name"],
            [("Alice", "Computer Science")],
        )
        assert response.status_code == 200

    def test_aggregate_query(self):
        response = self._make_request(
            "SELECT COUNT(*) FROM students",
            ["COUNT(*)"],
            [(3,)],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["rows"] == [[3]]

    def test_response_includes_validation_field(self):
        response = self._make_request(
            "SELECT * FROM students",
            ["id"],
            [(1,)],
        )
        data = response.json()
        assert "validation" in data
        assert data["validation"]["valid"] is True

    def test_request_id_in_response(self):
        response = self._make_request(
            "SELECT * FROM students",
            ["id"],
            [(1,)],
            headers={"X-Request-ID": "exec-trace-123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["request_id"] == "exec-trace-123"

    def test_x_request_id_header_in_response(self):
        response = self._make_request(
            "SELECT * FROM students",
            ["id"],
            [(1,)],
        )
        assert "x-request-id" in response.headers

    def test_null_values_preserved(self):
        response = self._make_request(
            "SELECT name, marks FROM students",
            ["name", "marks"],
            [("Alice", 95), ("Bob", None)],
        )
        data = response.json()
        assert data["rows"][1][1] is None

    def test_correct_column_names(self):
        response = self._make_request(
            "SELECT id, name, email FROM students",
            ["id", "name", "email"],
            [(1, "Alice", "alice@example.com")],
        )
        data = response.json()
        assert data["columns"] == ["id", "name", "email"]


# ===========================================================================
# 6. API ENDPOINT TESTS — SAFETY (REJECTION)
# ===========================================================================

class TestExecuteEndpointSafety:
    """Rejected statements must return 422 and never execute."""

    def _reject_request(self, sql):
        """Helper: post invalid SQL and expect 422."""
        mock_schema = _mock_schema()
        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            response = client.post(
                "/api/v1/query/execute",
                json={"sql": sql},
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error_code"] == "SQL_VALIDATION_FAILED"
        # Engine connect should NOT have been called
        mock_engine.return_value.connect.assert_not_called()
        return detail

    def test_insert_rejected(self):
        self._reject_request("INSERT INTO students (name) VALUES ('Alice')")

    def test_update_rejected(self):
        self._reject_request("UPDATE students SET name = 'Bob' WHERE id = 1")

    def test_delete_rejected(self):
        self._reject_request("DELETE FROM students WHERE id = 1")

    def test_drop_rejected(self):
        self._reject_request("DROP TABLE students")

    def test_alter_rejected(self):
        self._reject_request("ALTER TABLE students ADD COLUMN age INT")

    def test_create_rejected(self):
        self._reject_request("CREATE TABLE test_table (id INT)")

    def test_truncate_rejected(self):
        self._reject_request("TRUNCATE TABLE students")

    def test_multiple_statements_rejected(self):
        self._reject_request("SELECT * FROM students; DROP TABLE students;")

    def test_unknown_table_rejected(self):
        detail = self._reject_request("SELECT * FROM fake_table")
        assert "fake_table" in detail["message"]

    def test_unknown_column_rejected(self):
        self._reject_request("SELECT fake_col FROM students")


# ===========================================================================
# 7. CRITICAL — invalid SQL never reaches execute()
# ===========================================================================

class TestInvalidSQLNeverExecutes:
    """Prove that invalid SQL never reaches the database execution call."""

    def test_delete_never_reaches_db(self):
        """DELETE must be caught by validation, execute() never called."""
        mock_schema = _mock_schema()
        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            service = SQLExecutionService()
            result = service.execute("DELETE FROM students WHERE id = 1")

        assert result["validation"].valid is False
        mock_engine.return_value.connect.assert_not_called()

    def test_drop_never_reaches_db(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            service = SQLExecutionService()
            result = service.execute("DROP TABLE students")

        assert result["validation"].valid is False
        mock_engine.return_value.connect.assert_not_called()

    def test_stacked_injection_never_reaches_db(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            service = SQLExecutionService()
            result = service.execute(
                "SELECT * FROM students; INSERT INTO students (name) VALUES ('x');"
            )

        assert result["validation"].valid is False
        mock_engine.return_value.connect.assert_not_called()


# ===========================================================================
# 8. ERROR HANDLING
# ===========================================================================

class TestExecuteEndpointErrors:
    """Error handling tests for the execution endpoint."""

    def test_db_connection_failure_returns_500(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.side_effect = OperationalError(
                "statement", {}, Exception("connection refused")
            )

            response = client.post(
                "/api/v1/query/execute",
                json={"sql": "SELECT * FROM students"},
            )

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["error_code"] == "SQL_EXECUTION_FAILED"

    def test_schema_retrieval_failure_returns_500(self):
        with patch("app.services.sql_execution_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.side_effect = SQLAlchemyError("db down")

            response = client.post(
                "/api/v1/query/execute",
                json={"sql": "SELECT * FROM students"},
            )

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["error_code"] == "SCHEMA_RETRIEVAL_FAILED"

    def test_error_response_has_request_id(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine"),
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            response = client.post(
                "/api/v1/query/execute",
                json={"sql": "DROP TABLE students"},
                headers={"X-Request-ID": "err-trace-456"},
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["request_id"] == "err-trace-456"

    def test_api_key_not_in_error_response(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine"),
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            response = client.post(
                "/api/v1/query/execute",
                json={"sql": "DROP TABLE students"},
            )

        text = response.text
        assert "your_gemini_api_key_here" not in text
        assert "GEMINI_API_KEY" not in text

    def test_empty_sql_returns_422(self):
        response = client.post(
            "/api/v1/query/execute",
            json={"sql": ""},
        )
        assert response.status_code == 422

    def test_missing_sql_field_returns_422(self):
        response = client.post(
            "/api/v1/query/execute",
            json={},
        )
        assert response.status_code == 422

    def test_db_password_not_in_error(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.side_effect = OperationalError(
                "statement", {}, Exception("connection failed")
            )

            response = client.post(
                "/api/v1/query/execute",
                json={"sql": "SELECT * FROM students"},
            )

        text = response.text
        assert settings.DB_PASSWORD not in text or settings.DB_PASSWORD == ""


# ===========================================================================
# 9. CONFIGURATION TESTS
# ===========================================================================

class TestExecutionConfig:
    """Configuration settings for Phase 4B."""

    def test_sql_max_rows_setting_exists(self):
        assert hasattr(settings, "SQL_MAX_ROWS")
        assert isinstance(settings.SQL_MAX_ROWS, int)

    def test_sql_max_rows_default(self):
        # Default should be 1000 (may be overridden in .env)
        assert settings.SQL_MAX_ROWS > 0

    def test_sql_query_timeout_exists(self):
        assert hasattr(settings, "SQL_QUERY_TIMEOUT_SECONDS")
        assert isinstance(settings.SQL_QUERY_TIMEOUT_SECONDS, int)

    def test_sql_query_timeout_default(self):
        assert settings.SQL_QUERY_TIMEOUT_SECONDS > 0

    def test_max_rows_appears_in_response(self):
        mock_schema = _mock_schema()
        mock_result = _mock_db_result(["id"], [(1,)])

        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result

            response = client.post(
                "/api/v1/query/execute",
                json={"sql": "SELECT id FROM students"},
            )

        data = response.json()
        assert "max_rows" in data
        assert data["max_rows"] > 0


# ===========================================================================
# 10. ENDPOINT REGISTRATION
# ===========================================================================

class TestEndpointRegistration:
    """Verify the execution endpoint is properly registered."""

    def test_execute_endpoint_in_openapi(self):
        paths = list(app.openapi()["paths"].keys())
        assert "/api/v1/query/execute" in paths

    def test_all_existing_routes_still_registered(self):
        paths = list(app.openapi()["paths"].keys())
        assert "/health" in paths
        assert "/schema" in paths
        assert "/api/v1/health" in paths
        assert "/api/v1/schema" in paths
        assert "/api/v1/schema/{table_name}" in paths
        assert "/api/v1/query/generate" in paths
        assert "/api/v1/query/execute" in paths


# ===========================================================================
# 11. REGRESSION — Phases 1-4A still work
# ===========================================================================

class TestRegressionExistingPhases:
    """All previous phases must continue working."""

    def test_phase1_legacy_schema(self):
        mock_schema = _mock_schema()
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_database_schema.return_value = mock_schema
            response = client.get("/schema")
        assert response.status_code == 200

    def test_phase2_health(self):
        with patch("app.api.v1.health_routes.verify_connection", return_value=True):
            response = client.get("/api/v1/health")
        assert response.status_code == 200

    def test_phase3_generate(self):
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

    def test_phase4a_validation_still_integrated(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT name FROM students;"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show student names"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["validation"]["valid"] is True


# ===========================================================================
# 12. PHASE BOUNDARY
# ===========================================================================

class TestPhaseBoundary4B:
    """Verify Phase 4B does not introduce future-phase functionality."""

    def test_no_explain_in_execution_service(self):
        import inspect
        source = inspect.getsource(SQLExecutionService)
        assert "EXPLAIN" not in source

    def test_no_optimization_in_execution_service(self):
        import inspect
        source = inspect.getsource(SQLExecutionService)
        source_lower = source.lower()
        assert "optimiz" not in source_lower

    def test_no_explain_in_execution_service_code(self):
        """Phase 4B execution service should not contain EXPLAIN logic."""
        import inspect
        from app.services.sql_execution_service import SQLExecutionService
        source = inspect.getsource(SQLExecutionService.execute)
        assert "EXPLAIN" not in source

    def test_no_benchmark_endpoint(self):
        """Phase 4B execution endpoint does not automatically benchmark queries.

        The dedicated /api/v1/query/performance endpoint (Phase 4E) now exists,
        but it is a separate, user-triggered endpoint. The execution service
        itself must NOT perform benchmarking -- it should execute the query only
        once and return the result.
        """
        # The execution service should execute the query only once, not in a loop
        import inspect
        source = inspect.getsource(SQLExecutionService.execute)
        # Execution service must not contain timing loops or benchmark logic
        assert "run_count" not in source
        assert "warmup" not in source
        assert "perf_counter" not in source
