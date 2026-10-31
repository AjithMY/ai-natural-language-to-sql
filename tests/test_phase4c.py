"""
Phase 4C tests: MySQL Query Plan Analysis.

Tests cover:
  1.  Request/response model validation
  2.  EXPLAIN plan row parsing and serialization
  3.  Plan analysis logic (observations)
  4.  Service unit tests with mocked DB
  5.  Endpoint tests — valid EXPLAIN queries
  6.  Endpoint tests — safety/rejection (non-SELECT, unknown table, etc.)
  7.  Critical — prove invalid SQL never reaches the EXPLAIN DB call
  8.  Critical — prove the original SELECT is never executed
  9.  Error handling (DB failures, safe messages, request_id, no API key)
 10.  Endpoint registration
 11.  Regression — Phases 1–4B still work
 12.  Phase boundary — no optimization, no execution, no rewrite

All database calls are mocked — tests do NOT require a live MySQL connection.
"""

import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.core.config import settings
from app.main import app
from app.models.query_models import (
    ExplainPlanRow,
    QueryExplainRequest,
    QueryExplainResponse,
    SQLValidationResult,
)
from app.models.schema_models import (
    ColumnSchema,
    DatabaseSchema,
    ForeignKeySchema,
    TableSchema,
    UniqueConstraintSchema,
)
from app.services.query_plan_service import QueryPlanService, QueryPlanError

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


def _mock_explain_result(rows: list[tuple], columns: list[str] | None = None):
    """Create a mock SQLAlchemy result for EXPLAIN output."""
    if columns is None:
        columns = [
            "id", "select_type", "table", "partitions", "type",
            "possible_keys", "key", "key_len", "ref", "rows",
            "filtered", "Extra",
        ]
    mock_result = MagicMock()
    mock_result.keys.return_value = columns
    mock_result.fetchall.return_value = rows
    return mock_result


# Standard EXPLAIN row for a simple full table scan
SIMPLE_EXPLAIN_ROW = (
    1, "SIMPLE", "students", None, "ALL",
    None, None, None, None, 3,
    100.0, "Using filesort",
)

# EXPLAIN row with index usage
INDEX_EXPLAIN_ROW = (
    1, "SIMPLE", "students", None, "ref",
    "fk_student_dept", "fk_student_dept", "5", "const", 2,
    100.0, None,
)

# EXPLAIN row for JOIN (departments side)
JOIN_EXPLAIN_ROW_DEPT = (
    1, "SIMPLE", "departments", None, "eq_ref",
    "PRIMARY", "PRIMARY", "4", "minor_project.s.department_id", 1,
    100.0, None,
)


# ===========================================================================
# 1. REQUEST MODEL TESTS
# ===========================================================================

class TestQueryExplainRequest:
    """Request model validation."""

    def test_accepts_valid_sql(self):
        req = QueryExplainRequest(sql="SELECT * FROM students")
        assert req.sql == "SELECT * FROM students"

    def test_empty_sql_rejected(self):
        with pytest.raises(ValidationError):
            QueryExplainRequest(sql="")

    def test_whitespace_sql_rejected(self):
        with pytest.raises(ValidationError):
            QueryExplainRequest(sql="   ")

    def test_sql_is_stripped(self):
        req = QueryExplainRequest(sql="  SELECT * FROM students  ")
        assert req.sql == "SELECT * FROM students"


# ===========================================================================
# 2. RESPONSE MODEL / EXPLAIN ROW TESTS
# ===========================================================================

class TestExplainPlanRow:
    """ExplainPlanRow model tests."""

    def test_all_fields_present(self):
        row = ExplainPlanRow(
            id=1,
            select_type="SIMPLE",
            table="students",
            partitions=None,
            type="ALL",
            possible_keys=None,
            key=None,
            key_len=None,
            ref=None,
            rows=100,
            filtered=100.0,
            extra="Using filesort",
        )
        assert row.id == 1
        assert row.select_type == "SIMPLE"
        assert row.table == "students"
        assert row.type == "ALL"
        assert row.rows == 100
        assert row.filtered == 100.0
        assert row.extra == "Using filesort"

    def test_all_nullable_fields(self):
        row = ExplainPlanRow()
        assert row.id is None
        assert row.select_type is None
        assert row.table is None
        assert row.type is None
        assert row.key is None
        assert row.rows is None
        assert row.filtered is None
        assert row.extra is None

    def test_serialization_preserves_nulls(self):
        row = ExplainPlanRow(id=1, table="students", type="ALL")
        data = row.model_dump()
        assert data["possible_keys"] is None
        assert data["key"] is None
        assert data["ref"] is None
        assert data["partitions"] is None


class TestQueryExplainResponse:
    """Response model serialization."""

    def test_response_serializes_correctly(self):
        resp = QueryExplainResponse(
            sql="SELECT * FROM students",
            plan=[
                ExplainPlanRow(id=1, select_type="SIMPLE", table="students", type="ALL", rows=3),
            ],
            analysis=["Full table scan on 'students'"],
            validation=SQLValidationResult(
                valid=True, sql="SELECT * FROM students", errors=[], warnings=[],
            ),
            request_id="req-123",
        )
        data = resp.model_dump()
        assert data["sql"] == "SELECT * FROM students"
        assert len(data["plan"]) == 1
        assert data["plan"][0]["table"] == "students"
        assert "Full table scan" in data["analysis"][0]
        assert data["validation"]["valid"] is True
        assert data["request_id"] == "req-123"

    def test_response_with_multiple_plan_rows(self):
        resp = QueryExplainResponse(
            sql="SELECT s.name, d.name FROM students s JOIN departments d ON s.department_id = d.id",
            plan=[
                ExplainPlanRow(id=1, select_type="SIMPLE", table="s", type="ALL", rows=3),
                ExplainPlanRow(id=1, select_type="SIMPLE", table="d", type="eq_ref", rows=1),
            ],
            analysis=[],
            validation=SQLValidationResult(
                valid=True,
                sql="SELECT s.name, d.name FROM students s JOIN departments d ON s.department_id = d.id",
                errors=[], warnings=[],
            ),
        )
        assert len(resp.plan) == 2

    def test_response_does_not_contain_sensitive_fields(self):
        resp = QueryExplainResponse(
            sql="SELECT 1",
            plan=[],
            analysis=[],
            validation=SQLValidationResult(valid=True, sql="SELECT 1", errors=[], warnings=[]),
        )
        data = resp.model_dump()
        assert "api_key" not in data
        assert "password" not in data
        assert "GEMINI_API_KEY" not in data


# ===========================================================================
# 3. PLAN ANALYSIS LOGIC TESTS
# ===========================================================================

class TestPlanAnalysis:
    """Test QueryPlanService._analyze_plan observation generation."""

    def test_full_table_scan_observation(self):
        rows = [ExplainPlanRow(id=1, table="students", type="ALL", rows=100)]
        analysis = QueryPlanService._analyze_plan(rows)
        assert any("Full table scan" in obs and "students" in obs for obs in analysis)

    def test_index_lookup_observation(self):
        rows = [ExplainPlanRow(id=1, table="students", type="ref", key="fk_dept", rows=5)]
        analysis = QueryPlanService._analyze_plan(rows)
        assert any("Index lookup" in obs and "fk_dept" in obs for obs in analysis)

    def test_unique_index_lookup_observation(self):
        rows = [ExplainPlanRow(id=1, table="departments", type="eq_ref", key="PRIMARY", rows=1)]
        analysis = QueryPlanService._analyze_plan(rows)
        assert any("Unique index lookup" in obs for obs in analysis)

    def test_range_scan_observation(self):
        rows = [ExplainPlanRow(id=1, table="students", type="range", key="idx_marks", rows=10)]
        analysis = QueryPlanService._analyze_plan(rows)
        assert any("Range scan" in obs for obs in analysis)

    def test_filesort_observation(self):
        rows = [ExplainPlanRow(id=1, table="students", type="ALL", extra="Using filesort", rows=3)]
        analysis = QueryPlanService._analyze_plan(rows)
        assert "Using filesort" in analysis

    def test_temporary_table_observation(self):
        rows = [ExplainPlanRow(id=1, table="students", type="ALL", extra="Using temporary; Using filesort", rows=3)]
        analysis = QueryPlanService._analyze_plan(rows)
        assert "Using temporary table" in analysis
        assert "Using filesort" in analysis

    def test_estimated_rows_observation(self):
        rows = [ExplainPlanRow(id=1, table="students", type="ALL", rows=42)]
        analysis = QueryPlanService._analyze_plan(rows)
        assert any("42 estimated rows" in obs for obs in analysis)

    def test_const_lookup_observation(self):
        rows = [ExplainPlanRow(id=1, table="students", type="const", key="PRIMARY", rows=1)]
        analysis = QueryPlanService._analyze_plan(rows)
        assert any("Constant lookup" in obs for obs in analysis)

    def test_covering_index_observation(self):
        rows = [ExplainPlanRow(id=1, table="students", type="index", key="idx_name", extra="Using index", rows=3)]
        analysis = QueryPlanService._analyze_plan(rows)
        assert any("Covering index" in obs for obs in analysis)

    def test_empty_plan_returns_empty_analysis(self):
        analysis = QueryPlanService._analyze_plan([])
        assert analysis == []


# ===========================================================================
# 4. SERVICE UNIT TESTS
# ===========================================================================

class TestQueryPlanServiceUnit:
    """Unit tests for QueryPlanService with mocked DB."""

    def test_valid_select_returns_plan(self):
        """A valid SELECT should produce EXPLAIN plan rows."""
        mock_schema = _mock_schema()
        mock_result = _mock_explain_result([SIMPLE_EXPLAIN_ROW])

        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result

            service = QueryPlanService()
            result = service.explain("SELECT name, marks FROM students ORDER BY marks DESC")

        assert result["validation"].valid is True
        assert len(result["plan"]) == 1
        assert result["plan"][0].table == "students"
        assert result["plan"][0].type == "ALL"
        assert len(result["analysis"]) > 0

    def test_invalid_sql_not_explained(self):
        """Invalid SQL must NOT reach EXPLAIN database call."""
        mock_schema = _mock_schema()

        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            service = QueryPlanService()
            result = service.explain("DELETE FROM students")

        assert result["validation"].valid is False
        assert result["plan"] == []
        assert result["analysis"] == []
        # Engine connect should NOT have been called
        mock_engine.return_value.connect.assert_not_called()

    def test_unknown_table_not_explained(self):
        """SQL referencing unknown table must NOT trigger EXPLAIN."""
        mock_schema = _mock_schema()

        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            service = QueryPlanService()
            result = service.explain("SELECT * FROM nonexistent_table")

        assert result["validation"].valid is False
        mock_engine.return_value.connect.assert_not_called()

    def test_explain_error_raises_safe_exception(self):
        """Database EXPLAIN failure should raise QueryPlanError."""
        mock_schema = _mock_schema()

        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.side_effect = OperationalError(
                "statement", {}, Exception("connection lost")
            )

            service = QueryPlanService()
            with pytest.raises(QueryPlanError):
                service.explain("SELECT * FROM students")

    def test_explain_sends_explain_prefix(self):
        """Verify that EXPLAIN is prepended to the SQL sent to the database."""
        mock_schema = _mock_schema()
        mock_result = _mock_explain_result([SIMPLE_EXPLAIN_ROW])

        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result

            service = QueryPlanService()
            service.explain("SELECT * FROM students")

        # Check that the SQL sent to conn.execute starts with "EXPLAIN"
        call_args = mock_conn.execute.call_args
        sql_sent = str(call_args[0][0])
        assert sql_sent.startswith("EXPLAIN")

    def test_multiple_plan_rows_parsed(self):
        """JOIN queries should produce multiple EXPLAIN plan rows."""
        mock_schema = _mock_schema()
        mock_result = _mock_explain_result([
            SIMPLE_EXPLAIN_ROW,
            JOIN_EXPLAIN_ROW_DEPT,
        ])

        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result

            service = QueryPlanService()
            result = service.explain(
                "SELECT s.name, d.name FROM students s "
                "JOIN departments d ON s.department_id = d.id"
            )

        assert len(result["plan"]) == 2

    def test_null_explain_values_handled(self):
        """EXPLAIN rows with many NULLs should be parsed cleanly."""
        null_row = (1, "SIMPLE", "students", None, "ALL", None, None, None, None, 3, 100.0, None)
        mock_schema = _mock_schema()
        mock_result = _mock_explain_result([null_row])

        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result

            service = QueryPlanService()
            result = service.explain("SELECT * FROM students")

        plan_row = result["plan"][0]
        assert plan_row.possible_keys is None
        assert plan_row.key is None
        assert plan_row.ref is None
        assert plan_row.extra is None


# ===========================================================================
# 5. API ENDPOINT TESTS — VALID EXPLAIN
# ===========================================================================

class TestExplainEndpointValid:
    """Endpoint tests with mocked valid EXPLAIN."""

    def _make_request(self, sql, explain_rows=None, headers=None):
        """Helper: post to /api/v1/query/explain with mocked EXPLAIN."""
        if explain_rows is None:
            explain_rows = [SIMPLE_EXPLAIN_ROW]
        mock_schema = _mock_schema()
        mock_result = _mock_explain_result(explain_rows)

        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result

            return client.post(
                "/api/v1/query/explain",
                json={"sql": sql},
                headers=headers or {},
            )

    def test_simple_select_returns_200(self):
        response = self._make_request("SELECT * FROM students")
        assert response.status_code == 200
        data = response.json()
        assert "plan" in data
        assert len(data["plan"]) >= 1

    def test_select_with_where(self):
        response = self._make_request("SELECT name FROM students WHERE marks > 80")
        assert response.status_code == 200

    def test_select_with_order_by(self):
        response = self._make_request("SELECT name FROM students ORDER BY marks DESC")
        assert response.status_code == 200

    def test_select_with_limit(self):
        response = self._make_request("SELECT name FROM students LIMIT 5")
        assert response.status_code == 200

    def test_select_with_join(self):
        response = self._make_request(
            "SELECT s.name, d.name FROM students s "
            "INNER JOIN departments d ON s.department_id = d.id",
            explain_rows=[SIMPLE_EXPLAIN_ROW, JOIN_EXPLAIN_ROW_DEPT],
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["plan"]) == 2

    def test_aggregate_query(self):
        response = self._make_request("SELECT COUNT(*) FROM students")
        assert response.status_code == 200

    def test_group_by_query(self):
        response = self._make_request(
            "SELECT department_id, COUNT(*) FROM students GROUP BY department_id"
        )
        assert response.status_code == 200

    def test_response_includes_validation_field(self):
        response = self._make_request("SELECT * FROM students")
        data = response.json()
        assert "validation" in data
        assert data["validation"]["valid"] is True

    def test_response_includes_analysis(self):
        response = self._make_request("SELECT * FROM students")
        data = response.json()
        assert "analysis" in data
        assert isinstance(data["analysis"], list)

    def test_request_id_in_response(self):
        response = self._make_request(
            "SELECT * FROM students",
            headers={"X-Request-ID": "explain-trace-123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["request_id"] == "explain-trace-123"

    def test_x_request_id_header_in_response(self):
        response = self._make_request("SELECT * FROM students")
        assert "x-request-id" in response.headers

    def test_plan_row_has_expected_fields(self):
        response = self._make_request("SELECT * FROM students")
        data = response.json()
        plan_row = data["plan"][0]
        assert "id" in plan_row
        assert "select_type" in plan_row
        assert "table" in plan_row
        assert "type" in plan_row
        assert "possible_keys" in plan_row
        assert "key" in plan_row
        assert "rows" in plan_row
        assert "filtered" in plan_row
        assert "extra" in plan_row


# ===========================================================================
# 6. API ENDPOINT TESTS — SAFETY (REJECTION)
# ===========================================================================

class TestExplainEndpointSafety:
    """Rejected statements must return 422 and never reach EXPLAIN."""

    def _reject_request(self, sql):
        """Helper: post invalid SQL and expect 422."""
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            response = client.post(
                "/api/v1/query/explain",
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
# 7. CRITICAL — invalid SQL never reaches EXPLAIN
# ===========================================================================

class TestInvalidSQLNeverReachesExplain:
    """Prove that invalid SQL never reaches the EXPLAIN database call."""

    def test_delete_never_reaches_explain(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            service = QueryPlanService()
            result = service.explain("DELETE FROM students WHERE id = 1")

        assert result["validation"].valid is False
        mock_engine.return_value.connect.assert_not_called()

    def test_drop_never_reaches_explain(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            service = QueryPlanService()
            result = service.explain("DROP TABLE students")

        assert result["validation"].valid is False
        mock_engine.return_value.connect.assert_not_called()

    def test_stacked_injection_never_reaches_explain(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            service = QueryPlanService()
            result = service.explain(
                "SELECT * FROM students; INSERT INTO students (name) VALUES ('x');"
            )

        assert result["validation"].valid is False
        mock_engine.return_value.connect.assert_not_called()


# ===========================================================================
# 8. CRITICAL — original SELECT is never executed
# ===========================================================================

class TestOriginalSelectNotExecuted:
    """Prove that the Phase 4C service does NOT execute the original SELECT."""

    def test_explain_does_not_import_execution_service(self):
        """QueryPlanService source code should not reference SQLExecutionService."""
        import inspect
        source = inspect.getsource(QueryPlanService)
        assert "SQLExecutionService" not in source

    def test_explain_only_sends_explain_sql(self):
        """The only SQL sent to the database must start with 'EXPLAIN'."""
        mock_schema = _mock_schema()
        mock_result = _mock_explain_result([SIMPLE_EXPLAIN_ROW])

        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result

            service = QueryPlanService()
            service.explain("SELECT * FROM students")

        # Only one execute call should have been made
        assert mock_conn.execute.call_count == 1
        sql_sent = str(mock_conn.execute.call_args[0][0])
        assert sql_sent.startswith("EXPLAIN"), f"Expected EXPLAIN prefix, got: {sql_sent}"
        # The bare SELECT must NOT appear as a separate call
        assert "EXPLAIN" in sql_sent


# ===========================================================================
# 9. ERROR HANDLING
# ===========================================================================

class TestExplainEndpointErrors:
    """Error handling tests for the EXPLAIN endpoint."""

    def test_db_connection_failure_returns_500(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.side_effect = OperationalError(
                "statement", {}, Exception("connection refused")
            )

            response = client.post(
                "/api/v1/query/explain",
                json={"sql": "SELECT * FROM students"},
            )

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["error_code"] == "EXPLAIN_FAILED"

    def test_schema_retrieval_failure_returns_500(self):
        with patch("app.services.query_plan_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.side_effect = SQLAlchemyError("db down")

            response = client.post(
                "/api/v1/query/explain",
                json={"sql": "SELECT * FROM students"},
            )

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["error_code"] == "SCHEMA_RETRIEVAL_FAILED"

    def test_error_response_has_request_id(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine"),
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            response = client.post(
                "/api/v1/query/explain",
                json={"sql": "DROP TABLE students"},
                headers={"X-Request-ID": "err-trace-789"},
            )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["request_id"] == "err-trace-789"

    def test_api_key_not_in_error_response(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine"),
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            response = client.post(
                "/api/v1/query/explain",
                json={"sql": "DROP TABLE students"},
            )

        text = response.text
        assert "your_gemini_api_key_here" not in text
        assert "GEMINI_API_KEY" not in text

    def test_empty_sql_returns_422(self):
        response = client.post(
            "/api/v1/query/explain",
            json={"sql": ""},
        )
        assert response.status_code == 422

    def test_missing_sql_field_returns_422(self):
        response = client.post(
            "/api/v1/query/explain",
            json={},
        )
        assert response.status_code == 422

    def test_db_password_not_in_error(self):
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.side_effect = OperationalError(
                "statement", {}, Exception("connection failed")
            )

            response = client.post(
                "/api/v1/query/explain",
                json={"sql": "SELECT * FROM students"},
            )

        text = response.text
        assert settings.DB_PASSWORD not in text or settings.DB_PASSWORD == ""


# ===========================================================================
# 10. ENDPOINT REGISTRATION
# ===========================================================================

class TestExplainEndpointRegistration:
    """Verify the EXPLAIN endpoint is properly registered."""

    def test_explain_endpoint_in_openapi(self):
        paths = list(app.openapi()["paths"].keys())
        assert "/api/v1/query/explain" in paths

    def test_all_existing_routes_still_registered(self):
        paths = list(app.openapi()["paths"].keys())
        assert "/health" in paths
        assert "/schema" in paths
        assert "/api/v1/health" in paths
        assert "/api/v1/schema" in paths
        assert "/api/v1/schema/{table_name}" in paths
        assert "/api/v1/query/generate" in paths
        assert "/api/v1/query/execute" in paths
        assert "/api/v1/query/explain" in paths


# ===========================================================================
# 11. REGRESSION — Phases 1–4B still work
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

    def test_phase4b_execute(self):
        """Phase 4B execution endpoint must still work."""
        mock_schema = _mock_schema()
        mock_result = MagicMock()
        mock_result.keys.return_value = ["id"]
        mock_result.fetchmany.return_value = [(1,)]

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
        assert response.status_code == 200


# ===========================================================================
# 12. PHASE BOUNDARY
# ===========================================================================

class TestPhaseBoundary4C:
    """Verify Phase 4C does not introduce future-phase functionality."""

    def test_no_optimization_in_plan_service(self):
        """Plan service methods should not contain optimization logic."""
        import inspect
        # Check the functional methods, not docstrings
        for method in (QueryPlanService.explain, QueryPlanService._run_explain, QueryPlanService._analyze_plan):
            source_lower = inspect.getsource(method).lower()
            # Exclude comment/docstring lines — check for actual optimization calls
            code_lines = [
                line for line in source_lower.splitlines()
                if line.strip() and not line.strip().startswith('#') and not line.strip().startswith('\"\"\"')
            ]
            code = "\n".join(code_lines)
            assert "optimize(" not in code
            assert "optimize_query" not in code

    def test_no_rewrite_in_plan_service(self):
        """Plan service methods should not contain SQL rewrite logic."""
        import inspect
        for method in (QueryPlanService.explain, QueryPlanService._run_explain, QueryPlanService._analyze_plan):
            source_lower = inspect.getsource(method).lower()
            code_lines = [
                line for line in source_lower.splitlines()
                if line.strip() and not line.strip().startswith('#') and not line.strip().startswith('\"\"\"')
            ]
            code = "\n".join(code_lines)
            assert "rewrite(" not in code
            assert "rewrite_query" not in code


    def test_optimize_endpoint_exists_after_4d(self):
        """Phase 4D optimize endpoint should now be registered."""
        paths = list(app.openapi()["paths"].keys())
        assert "/api/v1/query/optimize" in paths
        # No rewrite endpoints should exist
        for path in paths:
            assert "rewrite" not in path.lower()

    def test_no_benchmark_endpoint(self):
        """Phase 4C EXPLAIN endpoint does not perform benchmarking.

        The dedicated /api/v1/query/performance endpoint (Phase 4E) now exists,
        but it is a separate, user-triggered endpoint. The EXPLAIN service
        must NOT run performance benchmarks internally.
        """
        import inspect
        from app.services.query_plan_service import QueryPlanService
        source = inspect.getsource(QueryPlanService.analyze)
        # EXPLAIN service must not contain benchmarking logic
        assert "run_count" not in source
        assert "warmup" not in source
        assert "perf_counter" not in source
