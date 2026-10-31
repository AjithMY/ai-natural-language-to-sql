"""
Phase 4E tests: User-Triggered Query Performance Analysis.

Tests cover:
   1. Performance endpoint exists (POST /api/v1/query/performance).
   2. Valid SELECT can be benchmarked (service + API).
   3. Multiple runs are performed.
   4. Correct number of timings is returned.
   5. Average is calculated correctly.
   6. Minimum is calculated correctly.
   7. Maximum is calculated correctly.
   8. Median is calculated correctly.
   9. Warmup runs are excluded from statistics.
  10. Invalid SQL is rejected.
  11. DELETE is rejected.
  12. UPDATE is rejected.
  13. INSERT is rejected.
  14. DROP is rejected.
  15. ALTER is rejected.
  16. CREATE is rejected.
  17. TRUNCATE is rejected.
  18. Multi-statement SQL is rejected.
  19. SQL injection attempts are rejected.
  20. Existing SQL validation is actually invoked.
  21. Database execution errors are handled correctly.
  22. No API key or database credentials appear in responses.
  23. SQL generation does NOT automatically trigger performance analysis.
  24. Existing Execute Query behavior still works.
  25. Existing EXPLAIN behavior still works.
  26. Existing Optimize behavior still works.

All database calls are mocked -- tests do NOT require a live MySQL connection.
"""

import statistics
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.main import app
from app.models.query_models import (
    QueryPerformanceRequest,
    QueryPerformanceResponse,
    SQLValidationResult,
)
from app.models.schema_models import (
    ColumnSchema,
    DatabaseSchema,
    ForeignKeySchema,
    TableSchema,
    UniqueConstraintSchema,
)
from app.services.query_performance_service import (
    QueryPerformanceError,
    QueryPerformanceService,
)
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
        unique_constraints=[UniqueConstraintSchema(name="uq_dept_name", columns=["name"])],
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
        unique_constraints=[UniqueConstraintSchema(name="uq_email", columns=["email"])],
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
            UniqueConstraintSchema(name="uq_student_course", columns=["student_id", "course_id"])
        ],
    )
    return DatabaseSchema(
        database="minor_project",
        tables=[departments, students, courses, enrollments],
        table_count=4,
    )


def _make_mock_execute(timings_ms: list[float]):
    """
    Build a mock _execute_once that returns values from timings_ms in sequence.
    This lets us inject known timing values into unit tests.
    """
    iter_timings = iter(timings_ms)

    def _fake_execute_once(sql: str) -> float:
        return next(iter_timings)

    return _fake_execute_once


# ===========================================================================
# 1. REQUEST MODEL TESTS
# ===========================================================================

class TestQueryPerformanceRequest:
    """Request model validation."""

    def test_accepts_valid_sql(self):
        req = QueryPerformanceRequest(sql="SELECT * FROM students")
        assert req.sql == "SELECT * FROM students"

    def test_default_run_count_is_5(self):
        req = QueryPerformanceRequest(sql="SELECT id FROM students")
        assert req.run_count == 5

    def test_custom_run_count(self):
        req = QueryPerformanceRequest(sql="SELECT id FROM students", run_count=3)
        assert req.run_count == 3

    def test_empty_sql_rejected(self):
        with pytest.raises(ValidationError):
            QueryPerformanceRequest(sql="")

    def test_whitespace_sql_rejected(self):
        with pytest.raises(ValidationError):
            QueryPerformanceRequest(sql="   ")

    def test_sql_is_stripped(self):
        req = QueryPerformanceRequest(sql="  SELECT id FROM students  ")
        assert req.sql == "SELECT id FROM students"

    def test_run_count_zero_rejected(self):
        with pytest.raises(ValidationError):
            QueryPerformanceRequest(sql="SELECT id FROM students", run_count=0)

    def test_run_count_above_20_rejected(self):
        with pytest.raises(ValidationError):
            QueryPerformanceRequest(sql="SELECT id FROM students", run_count=21)

    def test_run_count_20_accepted(self):
        req = QueryPerformanceRequest(sql="SELECT id FROM students", run_count=20)
        assert req.run_count == 20

    def test_run_count_1_accepted(self):
        req = QueryPerformanceRequest(sql="SELECT id FROM students", run_count=1)
        assert req.run_count == 1


# ===========================================================================
# 2. RESPONSE MODEL TESTS
# ===========================================================================

class TestQueryPerformanceResponse:
    """Response model validation."""

    def test_response_model_fields(self):
        resp = QueryPerformanceResponse(
            sql="SELECT id FROM students",
            run_count=3,
            warmup_runs=1,
            timings_ms=[1.0, 2.0, 3.0],
            average_ms=2.0,
            median_ms=2.0,
            min_ms=1.0,
            max_ms=3.0,
            validation=SQLValidationResult(valid=True, sql="SELECT id FROM students"),
            request_id="test-123",
        )
        assert resp.sql == "SELECT id FROM students"
        assert resp.run_count == 3
        assert resp.warmup_runs == 1
        assert len(resp.timings_ms) == 3
        assert resp.average_ms == 2.0
        assert resp.median_ms == 2.0
        assert resp.min_ms == 1.0
        assert resp.max_ms == 3.0
        assert resp.request_id == "test-123"

    def test_no_gemini_key_in_response(self):
        resp = QueryPerformanceResponse(
            sql="SELECT id FROM students",
            run_count=1,
            warmup_runs=0,
            timings_ms=[1.5],
            average_ms=1.5,
            median_ms=1.5,
            min_ms=1.5,
            max_ms=1.5,
            validation=SQLValidationResult(valid=True, sql="SELECT id FROM students"),
        )
        resp_json = resp.model_dump_json()
        assert "GEMINI_API_KEY" not in resp_json
        assert "gemini_api_key" not in resp_json
        assert "api_key" not in resp_json.lower()


# ===========================================================================
# 3. SERVICE TESTS -- VALID SELECT BENCHMARKING
# ===========================================================================

class TestQueryPerformanceServiceValidSelect:
    """Test that a valid SELECT can be benchmarked and statistics are correct."""

    def test_endpoint_exists(self):
        """Test 1: Performance endpoint exists and responds (not 404)."""
        # Send a request that we expect to fail validation (no DB needed),
        # but NOT return 404 (which would indicate the route doesn't exist).
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            res = client.post(
                "/api/v1/query/performance",
                json={"sql": "SELECT id FROM students", "run_count": 1},
            )
        # Route exists: we should not get 404 or 405
        assert res.status_code != 404
        assert res.status_code != 405

    def test_valid_select_benchmarked(self):
        """Test 2: Valid SELECT can be benchmarked."""
        schema = _mock_schema()
        known_timings = [2.0, 3.0, 4.0, 5.0, 6.0]

        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            service._execute_once = _make_mock_execute(known_timings)
            result = service.benchmark("SELECT id FROM students", run_count=5)

        assert result["run_count"] == 5
        assert len(result["timings_ms"]) == 5

    def test_multiple_runs_are_performed(self):
        """Test 3: Multiple runs are performed."""
        schema = _mock_schema()
        known_timings = [1.0, 2.0, 3.0]

        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            service._execute_once = _make_mock_execute(known_timings)
            result = service.benchmark("SELECT id FROM students", run_count=3)

        assert result["run_count"] == 3

    def test_correct_number_of_timings_returned(self):
        """Test 4: Correct number of timings is returned."""
        schema = _mock_schema()
        known_timings = [10.0, 20.0, 30.0, 40.0]

        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            service._execute_once = _make_mock_execute(known_timings)
            result = service.benchmark("SELECT id FROM students", run_count=4)

        assert len(result["timings_ms"]) == 4

    def test_average_calculated_correctly(self):
        """Test 5: Average is calculated correctly."""
        schema = _mock_schema()
        known_timings = [10.0, 20.0, 30.0]

        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            service._execute_once = _make_mock_execute(known_timings)
            result = service.benchmark("SELECT id FROM students", run_count=3)

        expected_avg = (10.0 + 20.0 + 30.0) / 3
        assert abs(result["average_ms"] - expected_avg) < 1e-9

    def test_minimum_calculated_correctly(self):
        """Test 6: Minimum is calculated correctly."""
        schema = _mock_schema()
        known_timings = [5.5, 1.1, 8.8, 3.3]

        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            service._execute_once = _make_mock_execute(known_timings)
            result = service.benchmark("SELECT id FROM students", run_count=4)

        assert abs(result["min_ms"] - 1.1) < 1e-9

    def test_maximum_calculated_correctly(self):
        """Test 7: Maximum is calculated correctly."""
        schema = _mock_schema()
        known_timings = [5.5, 1.1, 8.8, 3.3]

        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            service._execute_once = _make_mock_execute(known_timings)
            result = service.benchmark("SELECT id FROM students", run_count=4)

        assert abs(result["max_ms"] - 8.8) < 1e-9

    def test_median_calculated_correctly_odd(self):
        """Test 8a: Median is calculated correctly for odd number of runs."""
        schema = _mock_schema()
        known_timings = [1.0, 3.0, 5.0]  # median = 3.0

        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            service._execute_once = _make_mock_execute(known_timings)
            result = service.benchmark("SELECT id FROM students", run_count=3)

        assert abs(result["median_ms"] - 3.0) < 1e-9

    def test_median_calculated_correctly_even(self):
        """Test 8b: Median is calculated correctly for even number of runs."""
        schema = _mock_schema()
        known_timings = [1.0, 3.0, 5.0, 7.0]  # median = (3+5)/2 = 4.0

        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            service._execute_once = _make_mock_execute(known_timings)
            result = service.benchmark("SELECT id FROM students", run_count=4)

        assert abs(result["median_ms"] - 4.0) < 1e-9

    def test_single_run_statistics(self):
        """Statistics work with run_count=1."""
        schema = _mock_schema()
        known_timings = [7.77]

        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            service._execute_once = _make_mock_execute(known_timings)
            result = service.benchmark("SELECT id FROM students", run_count=1)

        assert abs(result["average_ms"] - 7.77) < 1e-9
        assert abs(result["min_ms"] - 7.77) < 1e-9
        assert abs(result["max_ms"] - 7.77) < 1e-9
        assert abs(result["median_ms"] - 7.77) < 1e-9

    def test_statistics_match_python_stdlib(self):
        """Average, median, min, max must match Python's own calculations."""
        schema = _mock_schema()
        known_timings = [4.1, 2.3, 8.7, 1.5, 5.9]

        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            service._execute_once = _make_mock_execute(known_timings)
            result = service.benchmark("SELECT id FROM students", run_count=5)

        expected_avg = sum(known_timings) / len(known_timings)
        expected_median = statistics.median(known_timings)
        expected_min = min(known_timings)
        expected_max = max(known_timings)

        assert abs(result["average_ms"] - expected_avg) < 1e-9
        assert abs(result["median_ms"] - expected_median) < 1e-9
        assert abs(result["min_ms"] - expected_min) < 1e-9
        assert abs(result["max_ms"] - expected_max) < 1e-9


# ===========================================================================
# 4. WARMUP RUN TESTS
# ===========================================================================

class TestWarmupRuns:
    """Test 9: Warmup runs are excluded from statistics."""

    def test_warmup_runs_excluded_from_statistics(self):
        """Warmup runs must NOT appear in timings_ms or affect statistics."""
        schema = _mock_schema()
        # warmup=1 means 1 warmup + 3 timed runs
        # Provide 4 values: first is the warmup (should be discarded)
        all_timings = [999.0, 1.0, 2.0, 3.0]  # 999 = warmup (discarded)
        expected_timed = [1.0, 2.0, 3.0]

        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=1)
            service._execute_once = _make_mock_execute(all_timings)
            result = service.benchmark("SELECT id FROM students", run_count=3)

        assert result["warmup_runs"] == 1
        assert result["run_count"] == 3
        assert len(result["timings_ms"]) == 3
        # 999.0 must NOT appear in timings
        assert 999.0 not in result["timings_ms"]
        assert abs(result["average_ms"] - (sum(expected_timed) / len(expected_timed))) < 1e-9

    def test_zero_warmup_runs(self):
        """warmup_runs=0 means all execute_once calls are timed."""
        schema = _mock_schema()
        known_timings = [5.0, 10.0]

        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            service._execute_once = _make_mock_execute(known_timings)
            result = service.benchmark("SELECT id FROM students", run_count=2)

        assert result["warmup_runs"] == 0
        assert result["run_count"] == 2
        assert len(result["timings_ms"]) == 2

    def test_warmup_count_reported_in_result(self):
        """warmup_runs field is correctly reported in result dict."""
        schema = _mock_schema()
        all_timings = [0.1, 2.0, 3.0, 4.0]

        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=1)
            service._execute_once = _make_mock_execute(all_timings)
            result = service.benchmark("SELECT id FROM students", run_count=3)

        assert result["warmup_runs"] == 1


# ===========================================================================
# 5. SQL SAFETY REJECTION TESTS
# ===========================================================================

class TestSQLSafetyRejection:
    """Tests 10-19: Invalid and dangerous SQL must be rejected."""

    def _benchmark_invalid(self, sql: str) -> dict:
        """Helper: attempt to benchmark sql, return result dict."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            return service.benchmark(sql, run_count=3)

    def test_invalid_sql_rejected(self):
        """Test 10: Invalid SQL is rejected."""
        result = self._benchmark_invalid("NOT VALID SQL ###")
        assert not result["validation"].valid
        assert result["run_count"] == 0
        assert result["timings_ms"] == []

    def test_delete_rejected(self):
        """Test 11: DELETE is rejected."""
        result = self._benchmark_invalid("DELETE FROM students WHERE id = 1")
        assert not result["validation"].valid

    def test_update_rejected(self):
        """Test 12: UPDATE is rejected."""
        result = self._benchmark_invalid("UPDATE students SET marks = 0")
        assert not result["validation"].valid

    def test_insert_rejected(self):
        """Test 13: INSERT is rejected."""
        result = self._benchmark_invalid("INSERT INTO students (name) VALUES ('x')")
        assert not result["validation"].valid

    def test_drop_rejected(self):
        """Test 14: DROP is rejected."""
        result = self._benchmark_invalid("DROP TABLE students")
        assert not result["validation"].valid

    def test_alter_rejected(self):
        """Test 15: ALTER is rejected."""
        result = self._benchmark_invalid("ALTER TABLE students ADD COLUMN age INT")
        assert not result["validation"].valid

    def test_create_rejected(self):
        """Test 16: CREATE is rejected."""
        result = self._benchmark_invalid("CREATE TABLE foo (id INT)")
        assert not result["validation"].valid

    def test_truncate_rejected(self):
        """Test 17: TRUNCATE is rejected."""
        result = self._benchmark_invalid("TRUNCATE TABLE students")
        assert not result["validation"].valid

    def test_multi_statement_rejected(self):
        """Test 18: Multi-statement SQL is rejected."""
        result = self._benchmark_invalid(
            "SELECT id FROM students; DROP TABLE students"
        )
        assert not result["validation"].valid

    def test_sql_injection_rejected(self):
        """Test 19: SQL injection attempts are rejected."""
        result = self._benchmark_invalid(
            "SELECT id FROM students; DELETE FROM students --"
        )
        assert not result["validation"].valid

    def test_non_select_never_executes(self):
        """Non-SELECT statements must not reach _execute_once."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            mock_execute = MagicMock()
            service._execute_once = mock_execute
            service.benchmark("DELETE FROM students WHERE id = 1", run_count=3)
            mock_execute.assert_not_called()


# ===========================================================================
# 6. VALIDATOR INVOCATION TESTS
# ===========================================================================

class TestValidatorInvocation:
    """Test 20: Existing SQL validation is actually invoked."""

    def test_validator_is_called(self):
        """SQLValidatorService.validate must be called during benchmarking."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            with patch(
                "app.services.query_performance_service.SQLValidatorService"
            ) as MockValidator:
                mock_validate = MagicMock(
                    return_value=SQLValidationResult(valid=False, sql="SELECT id FROM students", errors=["table not found"])
                )
                MockValidator.return_value.validate = mock_validate
                service = QueryPerformanceService(warmup_runs=0)
                service.benchmark("SELECT id FROM students", run_count=3)

            mock_validate.assert_called_once()

    def test_valid_sql_passes_to_execution(self):
        """When validation passes, _execute_once is called run_count times."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)
            mock_execute = MagicMock(return_value=1.0)
            service._execute_once = mock_execute
            service.benchmark("SELECT id FROM students", run_count=4)
            assert mock_execute.call_count == 4


# ===========================================================================
# 7. ERROR HANDLING TESTS
# ===========================================================================

class TestErrorHandling:
    """Test 21: Database execution errors are handled correctly."""

    def test_db_error_raises_performance_error(self):
        """QueryPerformanceError is raised when _execute_once fails."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryPerformanceService(warmup_runs=0)

            def _raise_perf_error(sql: str) -> float:
                # Simulate the service converting a DB error to QueryPerformanceError
                raise QueryPerformanceError("Database execution failed during benchmarking.")

            service._execute_once = _raise_perf_error
            with pytest.raises(QueryPerformanceError):
                service.benchmark("SELECT id FROM students", run_count=3)

    def test_schema_retrieval_error_propagates(self):
        """SQLAlchemyError propagates when schema retrieval fails."""
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.side_effect = SQLAlchemyError(
                "DB connection failed"
            )
            service = QueryPerformanceService(warmup_runs=0)
            with pytest.raises(SQLAlchemyError):
                service.benchmark("SELECT id FROM students", run_count=3)


# ===========================================================================
# 8. SECRET EXPOSURE TESTS
# ===========================================================================

class TestSecretExposure:
    """Test 22: No API key or database credentials appear in responses."""

    def test_no_credentials_in_api_error_response(self):
        """Error responses must not expose DB credentials or API keys."""
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.side_effect = SQLAlchemyError(
                "connection refused"
            )
            res = client.post(
                "/api/v1/query/performance",
                json={"sql": "SELECT id FROM students", "run_count": 3},
            )

        assert res.status_code == 500
        body = res.text
        assert "GEMINI_API_KEY" not in body
        assert "DB_PASSWORD" not in body
        assert "gemini_api_key" not in body
        assert "traceback" not in body.lower()

    def test_no_credentials_in_validation_error_response(self):
        """Validation error responses must not expose credentials."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            res = client.post(
                "/api/v1/query/performance",
                json={"sql": "DELETE FROM students", "run_count": 3},
            )

        assert res.status_code == 422
        body = res.text
        assert "GEMINI_API_KEY" not in body
        assert "DB_PASSWORD" not in body

    def test_db_password_not_in_benchmark_response(self):
        """Successful benchmark responses must not expose DB password."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            with patch(
                "app.services.query_performance_service.get_engine"
            ) as MockEngine:
                mock_conn = MagicMock()
                mock_result = MagicMock()
                mock_result.fetchall.return_value = []
                mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                mock_conn.__exit__ = MagicMock(return_value=False)
                mock_conn.execute.return_value = mock_result
                MockEngine.return_value.connect.return_value = mock_conn

                res = client.post(
                    "/api/v1/query/performance",
                    json={"sql": "SELECT id FROM students", "run_count": 2},
                )

        assert res.status_code == 200
        body = res.text
        assert "DB_PASSWORD" not in body
        assert "GEMINI_API_KEY" not in body


# ===========================================================================
# 9. API ENDPOINT INTEGRATION TESTS
# ===========================================================================

class TestPerformanceAPIEndpoint:
    """Integration tests for POST /api/v1/query/performance."""

    def test_valid_select_returns_200(self):
        """Test: valid SELECT returns HTTP 200 with correct structure."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            with patch(
                "app.services.query_performance_service.get_engine"
            ) as MockEngine:
                mock_conn = MagicMock()
                mock_result = MagicMock()
                mock_result.fetchall.return_value = []
                mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                mock_conn.__exit__ = MagicMock(return_value=False)
                mock_conn.execute.return_value = mock_result
                MockEngine.return_value.connect.return_value = mock_conn

                res = client.post(
                    "/api/v1/query/performance",
                    json={"sql": "SELECT id FROM students", "run_count": 3},
                )

        assert res.status_code == 200
        data = res.json()
        assert data["run_count"] == 3
        assert len(data["timings_ms"]) == 3
        assert "average_ms" in data
        assert "median_ms" in data
        assert "min_ms" in data
        assert "max_ms" in data
        assert "validation" in data

    def test_delete_rejected_returns_422(self):
        """DELETE must return 422."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            res = client.post(
                "/api/v1/query/performance",
                json={"sql": "DELETE FROM students WHERE id = 1", "run_count": 3},
            )

        assert res.status_code == 422

    def test_update_rejected_returns_422(self):
        """UPDATE must return 422."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            res = client.post(
                "/api/v1/query/performance",
                json={"sql": "UPDATE students SET marks = 0", "run_count": 3},
            )

        assert res.status_code == 422

    def test_insert_rejected_returns_422(self):
        """INSERT must return 422."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            res = client.post(
                "/api/v1/query/performance",
                json={"sql": "INSERT INTO students (name) VALUES ('x')", "run_count": 3},
            )

        assert res.status_code == 422

    def test_drop_rejected_returns_422(self):
        """DROP must return 422."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            res = client.post(
                "/api/v1/query/performance",
                json={"sql": "DROP TABLE students", "run_count": 3},
            )

        assert res.status_code == 422

    def test_multi_statement_rejected_returns_422(self):
        """Multi-statement SQL must return 422."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            res = client.post(
                "/api/v1/query/performance",
                json={
                    "sql": "SELECT id FROM students; DROP TABLE students",
                    "run_count": 3,
                },
            )

        assert res.status_code == 422

    def test_schema_error_returns_500(self):
        """Schema retrieval failure returns HTTP 500."""
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.side_effect = SQLAlchemyError(
                "Cannot connect"
            )
            res = client.post(
                "/api/v1/query/performance",
                json={"sql": "SELECT id FROM students", "run_count": 3},
            )

        assert res.status_code == 500
        data = res.json()
        assert data["detail"]["error_code"] == "SCHEMA_RETRIEVAL_FAILED"

    def test_db_execution_error_returns_500(self):
        """Database execution failure during benchmarking returns HTTP 500."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            with patch(
                "app.services.query_performance_service.get_engine"
            ) as MockEngine:
                MockEngine.return_value.connect.side_effect = OperationalError(
                    "statement", {}, Exception("lost connection")
                )

                res = client.post(
                    "/api/v1/query/performance",
                    json={"sql": "SELECT id FROM students", "run_count": 3},
                )

        assert res.status_code == 500
        data = res.json()
        assert data["detail"]["error_code"] == "PERFORMANCE_BENCHMARK_FAILED"

    def test_error_response_contains_no_secrets(self):
        """Error response must not expose API key or DB credentials."""
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.side_effect = SQLAlchemyError(
                "connection refused"
            )
            res = client.post(
                "/api/v1/query/performance",
                json={"sql": "SELECT id FROM students", "run_count": 3},
            )

        assert res.status_code == 500
        body = res.text
        assert "GEMINI_API_KEY" not in body
        assert "DB_PASSWORD" not in body

    def test_request_id_in_response(self):
        """Valid response should include request_id field."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            with patch(
                "app.services.query_performance_service.get_engine"
            ) as MockEngine:
                mock_conn = MagicMock()
                mock_result = MagicMock()
                mock_result.fetchall.return_value = []
                mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                mock_conn.__exit__ = MagicMock(return_value=False)
                mock_conn.execute.return_value = mock_result
                MockEngine.return_value.connect.return_value = mock_conn

                res = client.post(
                    "/api/v1/query/performance",
                    json={"sql": "SELECT id FROM students", "run_count": 2},
                )

        assert res.status_code == 200
        data = res.json()
        assert "request_id" in data


# ===========================================================================
# 10. SQL GENERATION DOES NOT AUTOMATICALLY TRIGGER PERFORMANCE ANALYSIS
# ===========================================================================

class TestNoAutoPerformance:
    """Test 23: SQL generation must NOT automatically trigger performance analysis."""

    def test_generate_does_not_call_performance_service(self):
        """POST /api/v1/query/generate must not call QueryPerformanceService."""
        with patch(
            "app.services.query_performance_service.QueryPerformanceService"
        ) as MockPerf:
            with patch(
                "app.services.query_generation_service.GeminiService"
            ) as MockGemini:
                MockGemini.return_value.generate.return_value = (
                    "SELECT id FROM students",
                    "gemini-test",
                )
                with patch("app.services.query_generation_service.SchemaService") as MockSchema:
                    MockSchema.return_value.get_full_schema.return_value = _mock_schema()
                    client.post(
                        "/api/v1/query/generate",
                        json={"question": "Show all students"},
                    )

            MockPerf.return_value.benchmark.assert_not_called()

    def test_performance_endpoint_only_at_explicit_call(self):
        """Confirm performance endpoint only responds when directly called."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            with patch(
                "app.services.query_performance_service.get_engine"
            ) as MockEngine:
                mock_conn = MagicMock()
                mock_result = MagicMock()
                mock_result.fetchall.return_value = []
                mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                mock_conn.__exit__ = MagicMock(return_value=False)
                mock_conn.execute.return_value = mock_result
                MockEngine.return_value.connect.return_value = mock_conn

                # Only explicit POST to /query/performance should trigger benchmarking
                res = client.post(
                    "/api/v1/query/performance",
                    json={"sql": "SELECT id FROM students", "run_count": 2},
                )

        assert res.status_code == 200


# ===========================================================================
# 11. REGRESSION TESTS -- Existing phases still work
# ===========================================================================

class TestRegressionExistingPhases:
    """Tests 24-26: Existing Execute Query, EXPLAIN, and Optimize still work."""

    def test_execute_query_endpoint_still_works(self):
        """Test 24: Existing Execute Query behavior still works."""
        schema = _mock_schema()
        with patch("app.services.sql_execution_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            with patch("app.services.sql_execution_service.get_engine") as MockEngine:
                mock_conn = MagicMock()
                mock_result = MagicMock()
                mock_result.keys.return_value = ["id"]
                mock_result.fetchmany.return_value = [(1,)]
                mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                mock_conn.__exit__ = MagicMock(return_value=False)
                mock_conn.execute.return_value = mock_result
                MockEngine.return_value.connect.return_value = mock_conn

                res = client.post(
                    "/api/v1/query/execute",
                    json={"sql": "SELECT id FROM students"},
                )

        assert res.status_code == 200
        data = res.json()
        assert "rows" in data
        assert "columns" in data

    def test_explain_endpoint_still_works(self):
        """Test 25: Existing EXPLAIN behavior still works."""
        schema = _mock_schema()
        with patch("app.services.query_plan_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            with patch("app.services.query_plan_service.get_engine") as MockEngine:
                mock_conn = MagicMock()
                mock_row = MagicMock()
                mock_row._mapping = {
                    "id": 1,
                    "select_type": "SIMPLE",
                    "table": "students",
                    "partitions": None,
                    "type": "ALL",
                    "possible_keys": None,
                    "key": None,
                    "key_len": None,
                    "ref": None,
                    "rows": 3,
                    "filtered": 100.0,
                    "Extra": None,
                }
                mock_result = MagicMock()
                mock_result.fetchall.return_value = [mock_row]
                mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                mock_conn.__exit__ = MagicMock(return_value=False)
                mock_conn.execute.return_value = mock_result
                MockEngine.return_value.connect.return_value = mock_conn

                res = client.post(
                    "/api/v1/query/explain",
                    json={"sql": "SELECT id FROM students"},
                )

        assert res.status_code == 200
        data = res.json()
        assert "plan" in data

    def test_optimize_endpoint_still_works(self):
        """Test 26: Existing Optimize behavior still works."""
        schema = _mock_schema()
        with patch("app.services.query_optimization_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            res = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT id FROM students"},
            )

        assert res.status_code == 200
        data = res.json()
        assert "original_sql" in data
        assert "optimized_sql" in data


# ===========================================================================
# 12. VALIDATE NO FAKE TIMING VALUES
# ===========================================================================

class TestNoFakeTiming:
    """Performance numbers must come from real (or mocked) execution, not invented."""

    def test_timings_are_floats(self):
        """Each timing must be a float (real measured value)."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            with patch(
                "app.services.query_performance_service.get_engine"
            ) as MockEngine:
                mock_conn = MagicMock()
                mock_result = MagicMock()
                mock_result.fetchall.return_value = []
                mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                mock_conn.__exit__ = MagicMock(return_value=False)
                mock_conn.execute.return_value = mock_result
                MockEngine.return_value.connect.return_value = mock_conn

                service = QueryPerformanceService(warmup_runs=0)
                result = service.benchmark("SELECT id FROM students", run_count=3)

        for t in result["timings_ms"]:
            assert isinstance(t, float)
            assert t >= 0.0

    def test_all_stats_are_non_negative(self):
        """Average, median, min, max must all be non-negative."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            with patch(
                "app.services.query_performance_service.get_engine"
            ) as MockEngine:
                mock_conn = MagicMock()
                mock_result = MagicMock()
                mock_result.fetchall.return_value = []
                mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                mock_conn.__exit__ = MagicMock(return_value=False)
                mock_conn.execute.return_value = mock_result
                MockEngine.return_value.connect.return_value = mock_conn

                service = QueryPerformanceService(warmup_runs=0)
                result = service.benchmark("SELECT id FROM students", run_count=3)

        assert result["average_ms"] >= 0.0
        assert result["median_ms"] >= 0.0
        assert result["min_ms"] >= 0.0
        assert result["max_ms"] >= 0.0


# ===========================================================================
# 13. TEMPORARY SCHEMA & NON-EXISTENT TABLE BEHAVIOR
# ===========================================================================

class TestTemporarySchemaPerformance:
    """Preserve schema-clarification behavior: non-existent tables are not fabricated."""

    def test_temporary_schema_table_rejected_when_not_in_live_db(self):
        """If a query references a table from a temporary schema that does not exist in MySQL:
        - do not fabricate performance results
        - return the appropriate table-not-found / validation error
        - do not create the table in MySQL
        """
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            with patch("app.services.query_performance_service.get_engine") as MockEngine:
                service = QueryPerformanceService(warmup_runs=0)
                result = service.benchmark("SELECT * FROM temp_projects;", run_count=5)

                assert result["validation"].valid is False
                assert any("temp_projects" in err for err in result["validation"].errors)
                assert result["run_count"] == 0
                assert result["timings_ms"] == []
                assert result["average_ms"] == 0.0
                MockEngine.assert_not_called()

    def test_temporary_schema_api_returns_422_without_fabricating(self):
        """API endpoint returns 422 with SQL_VALIDATION_FAILED for unpersisted temporary table."""
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            res = client.post(
                "/api/v1/query/performance",
                json={"sql": "SELECT * FROM temp_projects;", "run_count": 5},
            )

        assert res.status_code == 422
        data = res.json()
        assert data["detail"]["error_code"] == "SQL_VALIDATION_FAILED"
        assert "temp_projects" in data["detail"]["message"]

    def test_table_not_found_db_error_is_not_fabricated(self):
        """If the database raises a table-not-found error during benchmarking,
        it must raise QueryPerformanceError with a safe error message and not fabricate results.
        """
        schema = _mock_schema()
        with patch("app.services.query_performance_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            with patch("app.services.query_performance_service.get_engine") as MockEngine:
                mock_conn = MagicMock()
                mock_conn.__enter__ = MagicMock(return_value=mock_conn)
                mock_conn.__exit__ = MagicMock(return_value=False)
                from sqlalchemy.exc import OperationalError
                mock_conn.execute.side_effect = OperationalError("Table 'college_db.temp_projects' doesn't exist", None, None)
                MockEngine.return_value.connect.return_value = mock_conn

                service = QueryPerformanceService(warmup_runs=0)
                with pytest.raises(QueryPerformanceError) as exc_info:
                    service.benchmark("SELECT id FROM students", run_count=5)

                assert "Database execution failed during benchmarking" in str(exc_info.value)


# ===========================================================================
# 14. REAL MYSQL PERFORMANCE VERIFICATION (WHEN CONNECTED)
# ===========================================================================

class TestLiveMySQLPerformance:
    """Verifies actual execution against live MySQL when available."""

    def test_live_mysql_benchmark_if_connected(self):
        """Execute a real SELECT against the live database if available."""
        from app.database.connection import verify_connection
        if not verify_connection():
            pytest.skip("Live MySQL database is not connected.")

        service = QueryPerformanceService(warmup_runs=1)
        res = service.benchmark("SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;", run_count=5)

        assert res["validation"].valid is True
        assert res["run_count"] == 5
        assert res["warmup_runs"] == 1
        assert len(res["timings_ms"]) == 5
        assert all(t > 0.0 for t in res["timings_ms"])
        assert res["average_ms"] > 0.0
        assert res["min_ms"] <= res["median_ms"] <= res["max_ms"]
        assert res["min_ms"] <= res["average_ms"] <= res["max_ms"]


