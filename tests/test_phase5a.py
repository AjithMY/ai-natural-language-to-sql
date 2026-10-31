"""
Phase 5A: Frontend / Full System Integration Test Suite.

Verifies the complete integrated workflow across:
  1. Frontend static asset delivery and root redirect.
  2. Integration of all 5 query endpoints:
     - POST /api/v1/query/generate
     - POST /api/v1/query/execute
     - POST /api/v1/query/explain
     - POST /api/v1/query/optimize
     - POST /api/v1/query/performance
  3. Strict independence of operations (no automatic execution, EXPLAIN, optimization, or benchmarking).
  4. Schema display: metadata only, no data rows, correct source attribution (live vs user_provided).
  5. Schema clarification flow for missing tables/columns.
  6. Temporary schema behavior: no table creation in MySQL, no data fabrication.
  7. Consistent and secure error handling without leaking secrets or credentials.
  8. OpenAPI contract compliance (no frontend paths exposed in OpenAPI).
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.main import app
from app.models.schema_models import (
    ColumnSchema,
    DatabaseSchema,
    ForeignKeySchema,
    TableSchema,
)

client = TestClient(app)


def _mock_schema() -> DatabaseSchema:
    """Return a mock live DatabaseSchema with departments and students."""
    dept = TableSchema(
        name="departments",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="name", type="VARCHAR(100)", nullable=False),
        ],
        primary_keys=["id"],
        foreign_keys=[],
        source="live",
    )
    stud = TableSchema(
        name="students",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="name", type="VARCHAR(100)", nullable=False),
            ColumnSchema(name="marks", type="INTEGER", nullable=True),
            ColumnSchema(name="department_id", type="INTEGER", nullable=True),
        ],
        primary_keys=["id"],
        foreign_keys=[
            ForeignKeySchema(
                column="department_id",
                references_table="departments",
                references_column="id",
                constraint_name="fk_stud_dept",
            )
        ],
        source="live",
    )
    return DatabaseSchema(database="college_db", tables=[dept, stud], table_count=2)


# ===========================================================================
# 1. FRONTEND STATIC ASSETS & ROOT REDIRECT
# ===========================================================================

class TestFrontendStaticDelivery:
    """Verify frontend assets are served correctly and root redirects properly."""

    def test_root_redirects_to_frontend(self):
        """Root URL GET / must redirect to /frontend/index.html."""
        res = client.get("/", follow_redirects=False)
        assert res.status_code in (301, 302, 307, 308)
        assert "/frontend/index.html" in res.headers.get("location", "")

    def test_frontend_index_html_loads(self):
        """GET /frontend/index.html returns 200 and contains core dashboard elements."""
        res = client.get("/frontend/index.html")
        assert res.status_code == 200
        html = res.text
        assert "NL2SQL" in html
        assert "questionInput" in html
        assert "generateBtn" in html
        assert "executeBtn" in html
        assert "analyzeBtn" in html
        assert "optimizeBtn" in html
        assert "performanceBtn" in html
        assert "copySqlBtn" in html
        assert "missingSchemaSection" in html
        assert "schemaSection" in html

    def test_frontend_app_js_loads(self):
        """GET /frontend/app.js returns 200 and includes all required action functions."""
        res = client.get("/frontend/app.js")
        assert res.status_code == 200
        js = res.text
        assert "function generateSQL" in js
        assert "function executeQuery" in js
        assert "function analyzeQuery" in js
        assert "explainQuery" in js
        assert "function optimizeQuery" in js
        assert "function analyzePerformance" in js
        assert "function copySQL" in js

    def test_frontend_styles_css_loads(self):
        """GET /frontend/styles.css returns 200 and includes styling tokens."""
        res = client.get("/frontend/styles.css")
        assert res.status_code == 200
        css = res.text
        assert "--bg-primary" in css
        assert ".btn-execute" in css
        assert ".btn-performance" in css
        assert ".btn-copy" in css

    def test_no_frontend_routes_in_openapi(self):
        """OpenAPI schema must NOT expose internal frontend routes."""
        paths = list(app.openapi()["paths"].keys())
        for path in paths:
            assert "frontend" not in path.lower()
            assert "dashboard" not in path.lower()


# ===========================================================================
# 2. GENERATE SQL WORKFLOW & INDEPENDENCE
# ===========================================================================

class TestGenerateSQLWorkflow:
    """Verify SQL generation workflow stops after generation and exposes metadata only."""

    def test_generate_sql_success_stops_without_execution(self):
        """Generate SQL returns valid SQL and stops; does not execute or explain."""
        schema = _mock_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
            patch("app.services.sql_execution_service.SQLExecutionService.execute") as mock_exec,
            patch("app.services.query_plan_service.QueryPlanService.explain") as mock_explain,
            patch("app.services.query_optimization_service.QueryOptimizationService.optimize") as mock_opt,
            patch("app.services.query_performance_service.QueryPerformanceService.benchmark") as mock_perf,
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            MockGemini.return_value.generate_sql.return_value = (
                "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;"
            )

            res = client.post(
                "/api/v1/query/generate",
                json={"question": "Show top 5 students with highest marks"},
            )

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "GENERATED"
        assert "SELECT name, marks FROM students" in data["sql"]
        assert data["validation"]["valid"] is True
        assert len(data["relevant_schemas"]) > 0

        # Verify strict independence: no subsequent actions called automatically
        mock_exec.assert_not_called()
        mock_explain.assert_not_called()
        mock_opt.assert_not_called()
        mock_perf.assert_not_called()

    def test_schema_metadata_only_no_data_rows(self):
        """Relevant schema must contain column types, PKs, FKs, but NEVER data rows."""
        schema = _mock_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            MockGemini.return_value.generate_sql.return_value = (
                "SELECT name, marks FROM students;"
            )

            res = client.post(
                "/api/v1/query/generate",
                json={"question": "List all students"},
            )

        assert res.status_code == 200
        data = res.json()
        relevant = data["relevant_schemas"]
        assert len(relevant) >= 1
        for table in relevant:
            assert "name" in table
            assert "columns" in table
            assert "primary_keys" in table
            assert "source" in table
            assert "rows" not in table
            assert "data" not in table
            assert table["source"] == "live"


# ===========================================================================
# 3. SCHEMA CLARIFICATION & TEMPORARY SCHEMA FLOW
# ===========================================================================

class TestSchemaClarificationWorkflow:
    """Verify missing schema triggers clarification and temporary schema is metadata-only."""

    def test_missing_table_triggers_clarification(self):
        """When an unknown table is referenced, status is SCHEMA_REQUIRED and no SQL is generated."""
        schema = _mock_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            res = client.post(
                "/api/v1/query/generate",
                json={"question": "Give me all employee details with salary"},
            )

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "SCHEMA_REQUIRED"
        assert data["sql"] == ""
        assert data["missing_schema"] is not None
        assert "employees" in data["missing_schema"]["missing_tables"]
        MockGemini.return_value.generate_sql.assert_not_called()

    def test_temporary_schema_generation_and_source_tagging(self):
        """Providing user schema produces SQL tagged as user_provided/combined schema."""
        schema = _mock_schema()
        user_schema = (
            "employees\n"
            "- id INT PRIMARY KEY\n"
            "- name VARCHAR(100)\n"
            "- salary DECIMAL(10,2)"
        )
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            MockGemini.return_value.generate_sql.return_value = (
                "SELECT id, name, salary FROM employees;"
            )

            res = client.post(
                "/api/v1/query/generate",
                json={
                    "question": "Show all employees with their salary",
                    "user_schema_text": user_schema,
                },
            )

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "GENERATED"
        assert data["schema_source"] in ("user_provided", "combined")
        sources = [t["source"] for t in data["relevant_schemas"]]
        assert "user_provided" in sources

    def test_temporary_schema_execution_does_not_modify_mysql(self):
        """Executing SQL with an unpersisted table fails safely with real DB error without modifying MySQL."""
        schema = _mock_schema()
        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as MockEngine,
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            # Validation against live schema fails because employees does not exist in live DB
            res = client.post(
                "/api/v1/query/execute",
                json={"sql": "SELECT name, salary FROM employees;"},
            )

        assert res.status_code == 422
        data = res.json()
        assert data["detail"]["error_code"] == "SQL_VALIDATION_FAILED"
        assert "employees" in data["detail"]["message"]
        MockEngine.assert_not_called()


# ===========================================================================
# 4. INDEPENDENT ACTION ENDPOINTS INTEGRATION
# ===========================================================================

class TestActionEndpointsIntegration:
    """Verify Execute, Explain, Optimize, and Performance operate independently."""

    def test_execute_query_endpoint(self):
        """Explicit POST /api/v1/query/execute returns result rows and row_count."""
        schema = _mock_schema()
        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as MockEngine,
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            mock_conn = MagicMock()
            mock_result = MagicMock()
            mock_result.keys.return_value = ["name", "marks"]
            mock_result.fetchmany.return_value = [("Alice", 95), ("Bob", 88)]
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result
            MockEngine.return_value.connect.return_value = mock_conn

            res = client.post(
                "/api/v1/query/execute",
                json={"sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 2;"},
            )

        assert res.status_code == 200
        data = res.json()
        assert data["columns"] == ["name", "marks"]
        assert len(data["rows"]) == 2
        assert data["row_count"] == 2

    def test_explain_query_endpoint(self):
        """Explicit POST /api/v1/query/explain returns query plan without executing SELECT."""
        schema = _mock_schema()
        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as MockEngine,
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            mock_conn = MagicMock()
            mock_result = MagicMock()
            mock_result.keys.return_value = [
                "id", "select_type", "table", "partitions", "type",
                "possible_keys", "key", "key_len", "ref", "rows",
                "filtered", "Extra",
            ]
            mock_result.fetchall.return_value = [
                (1, "SIMPLE", "students", None, "ALL", None, None, None, None, 10, 100.0, "Using filesort"),
            ]
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result
            MockEngine.return_value.connect.return_value = mock_conn

            res = client.post(
                "/api/v1/query/explain",
                json={"sql": "SELECT name, marks FROM students ORDER BY marks DESC;"},
            )

        assert res.status_code == 200
        data = res.json()
        assert "plan" in data
        assert len(data["plan"]) == 1
        assert data["plan"][0]["table"] == "students"
        assert "analysis" in data

    def test_optimize_query_endpoint(self):
        """Explicit POST /api/v1/query/optimize returns optimized SQL and rule list."""
        schema = _mock_schema()
        with patch("app.services.query_optimization_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            res = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT * FROM students;"},
            )

        assert res.status_code == 200
        data = res.json()
        assert data["original_sql"] == "SELECT * FROM students;"
        assert "optimized_sql" in data
        assert isinstance(data["optimizations"], list)

    def test_performance_query_endpoint(self):
        """Explicit POST /api/v1/query/performance returns real measured timing statistics."""
        schema = _mock_schema()
        with (
            patch("app.services.query_performance_service.SchemaService") as MockSchema,
            patch("app.services.query_performance_service.get_engine") as MockEngine,
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            mock_conn = MagicMock()
            mock_result = MagicMock()
            mock_result.fetchall.return_value = [("Alice", 95)]
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result
            MockEngine.return_value.connect.return_value = mock_conn

            res = client.post(
                "/api/v1/query/performance",
                json={"sql": "SELECT name, marks FROM students LIMIT 1;", "run_count": 3},
            )

        assert res.status_code == 200
        data = res.json()
        assert data["run_count"] == 3
        assert len(data["timings_ms"]) == 3
        assert data["average_ms"] >= 0.0
        assert data["median_ms"] >= 0.0
        assert data["min_ms"] >= 0.0
        assert data["max_ms"] >= 0.0


# ===========================================================================
# 5. SECURITY, CREDENTIAL REDACTION & ERROR HANDLING
# ===========================================================================

class TestSecurityAndErrorHandling:
    """Verify credentials and internal traces are never exposed in error responses."""

    def test_unsafe_sql_rejected_across_all_endpoints(self):
        """Harmful SQL statements are rejected with 422 across all query endpoints."""
        harmful_sql = "DROP TABLE students;"
        endpoints = [
            "/api/v1/query/execute",
            "/api/v1/query/explain",
            "/api/v1/query/optimize",
            "/api/v1/query/performance",
        ]
        schema = _mock_schema()

        for ep in endpoints:
            with (
                patch("app.services.sql_execution_service.SchemaService") as S1,
                patch("app.services.query_plan_service.SchemaService") as S2,
                patch("app.services.query_optimization_service.SchemaService") as S3,
                patch("app.services.query_performance_service.SchemaService") as S4,
            ):
                S1.return_value.get_full_schema.return_value = schema
                S2.return_value.get_full_schema.return_value = schema
                S3.return_value.get_full_schema.return_value = schema
                S4.return_value.get_full_schema.return_value = schema

                res = client.post(ep, json={"sql": harmful_sql})
                assert res.status_code == 422, f"Endpoint {ep} did not reject harmful SQL"
                data = res.json()
                assert "detail" in data

    def test_no_secret_exposure_in_500_response(self):
        """Database connection failures return a safe message without passwords or connection URIs."""
        with patch("app.services.query_generation_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.side_effect = SQLAlchemyError(
                "Access denied for user 'root'@'localhost' (using password: YES)"
            )

            res = client.post(
                "/api/v1/query/generate",
                json={"question": "Show all students"},
            )

        assert res.status_code == 500
        text_lower = res.text.lower()
        assert "password: yes" not in text_lower
        assert "root@" not in text_lower
        assert "traceback" not in text_lower
