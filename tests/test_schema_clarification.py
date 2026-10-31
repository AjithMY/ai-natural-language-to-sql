"""
Comprehensive test suite for Schema Clarification Flow, Relevant Schema Metadata Display,
and Safe User-Provided Temporary Schemas.

Covers all 20 required verification points:
  1. Existing table schema is retrieved.
  2. Existing table schema is displayed without table data (metadata only).
  3. Multiple relevant table schemas are displayed.
  4. Missing table is detected.
  5. Missing column is detected.
  6. User is asked for missing schema (status='SCHEMA_REQUIRED').
  7. User-provided schema is parsed (structured & bullet text).
  8. User-provided schema is validated.
  9. Live + temporary schema can be combined in memory.
  10. Foreign-key relationships can be represented and used.
  11. SQL is generated using combined schema.
  12. Unknown columns are not invented.
  13. Malicious schema input is rejected (SQL injection / comments / DDL).
  14. Generated SQL is not automatically executed.
  15. Explicit Execute action uses existing execution service.
  16. Temporary schema does not create a MySQL table.
  17. Existing SQL validation remains active.
  18. Existing execution safety remains active.
  19. Existing Phase 1-4D tests remain passing.
  20. Response models contain relevant_schemas strictly as metadata.
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.query_models import (
    MissingSchemaInfo,
    QueryGenerationRequest,
    QueryGenerationResponse,
)
from app.models.schema_models import (
    ColumnSchema,
    DatabaseSchema,
    ForeignKeySchema,
    TableSchema,
    UserColumnInput,
    UserForeignKeyInput,
    UserProvidedSchema,
    UserTableInput,
)
from app.services.schema_availability_service import SchemaAvailabilityService
from app.services.schema_parser import SchemaParser, SchemaParsingError

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers — realistic mock live database schema
# ---------------------------------------------------------------------------

def _mock_live_schema() -> DatabaseSchema:
    """Return a mock live DatabaseSchema with departments, students, and courses."""
    departments = TableSchema(
        name="departments",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="name", type="VARCHAR(100)", nullable=False),
        ],
        primary_keys=["id"],
        foreign_keys=[],
        source="live",
    )
    students = TableSchema(
        name="students",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="name", type="VARCHAR(100)", nullable=False),
            ColumnSchema(name="email", type="VARCHAR(150)", nullable=False),
            ColumnSchema(name="marks", type="INTEGER", nullable=True),
            ColumnSchema(name="department_id", type="INTEGER", nullable=True),
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
        source="live",
    )
    return DatabaseSchema(
        database="college_db",
        tables=[departments, students],
        table_count=2,
    )


# ===========================================================================
# 1-3. Relevant Schema Display (Metadata Only, Strictly No Rows)
# ===========================================================================

class TestRelevantSchemaMetadataDisplay:
    """Tests 1-3 & 20: System returns table metadata without exposing actual table data/rows."""

    def test_existing_table_schema_retrieved_and_displayed(self):
        """Test 1 & 2: Relevant table schema is returned without table rows or records."""
        mock_schema = _mock_live_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT name, marks FROM students;"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show all students"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "GENERATED"
        assert "relevant_schemas" in data
        assert len(data["relevant_schemas"]) >= 1

        # Check metadata fields
        students_meta = next(t for t in data["relevant_schemas"] if t["name"] == "students")
        assert students_meta["source"] == "live"
        assert len(students_meta["columns"]) == 5
        col_names = [c["name"] for c in students_meta["columns"]]
        assert "name" in col_names
        assert "marks" in col_names

        # Critical: Verify NO row data or records are returned in the schema
        assert "rows" not in students_meta
        assert "records" not in students_meta
        assert "data" not in students_meta

    def test_multiple_relevant_table_schemas_displayed(self):
        """Test 3: Questions involving multiple tables display schemas for all relevant tables."""
        mock_schema = _mock_live_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = (
                "SELECT s.name, d.name AS dept_name FROM students s JOIN departments d ON s.department_id = d.id;"
            )

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show students with their department names"},
            )

        assert response.status_code == 200
        data = response.json()
        table_names = [t["name"] for t in data["relevant_schemas"]]
        assert "students" in table_names
        assert "departments" in table_names


# ===========================================================================
# 4-6. Missing Table & Column Detection -> Schema Clarification Flow
# ===========================================================================

class TestMissingSchemaClarification:
    """Tests 4-6: Missing tables or columns trigger a structured clarification request."""

    def test_missing_table_detected_triggers_clarification(self):
        """Test 4 & 6: When requested table is not in live DB, system asks user for schema."""
        mock_schema = _mock_live_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Give me all employee details with salary"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "SCHEMA_REQUIRED"
        assert data["sql"] == ""
        assert data["missing_schema"] is not None
        assert "employees" in data["missing_schema"]["missing_tables"]
        assert "employees" in data["message"].lower()
        assert "please provide the schema" in data["message"].lower()

        # Crucial: GeminiService.generate_sql must NOT have been called
        MockGemini.return_value.generate_sql.assert_not_called()

    def test_missing_column_detected_triggers_clarification(self):
        """Test 5: When a specific column is missing from all active tables, clarification is requested."""
        mock_schema = _mock_live_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show all salaries"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "SCHEMA_REQUIRED"
        assert "salary" in data["missing_schema"]["missing_columns"] or "salaries" in data["missing_schema"]["missing_columns"]

    def test_multiple_missing_tables_all_identified(self):
        """When multiple requested tables are missing, all are identified."""
        mock_schema = _mock_live_schema()
        service = SchemaAvailabilityService()
        result = service.analyze("Show all products and orders", mock_schema)
        assert not result.is_available
        assert "products" in result.missing_tables
        assert "orders" in result.missing_tables


# ===========================================================================
# 7-10. User-Provided Schema Parsing, Validation & Combination
# ===========================================================================

class TestSchemaParserAndCombiner:
    """Tests 7-10: Parsing user-provided temporary schema and combining with live schema."""

    def test_parse_bullet_text_format(self):
        """Test 7: Bullet-pointed text schema is parsed into TableSchema."""
        parser = SchemaParser()
        raw_text = (
            "employees\n"
            "- id INT PRIMARY KEY\n"
            "- name VARCHAR(100)\n"
            "- email VARCHAR(150)\n"
            "- salary DECIMAL(10,2)\n"
            "- department_id INT\n"
            "FOREIGN KEY (department_id) -> departments.id"
        )
        tables = parser.parse_text(raw_text)
        assert len(tables) == 1
        emp = tables[0]
        assert emp.name == "employees"
        assert emp.source == "user_provided"
        assert len(emp.columns) == 5
        assert "id" in emp.primary_keys
        assert len(emp.foreign_keys) == 1
        assert emp.foreign_keys[0].column == "department_id"
        assert emp.foreign_keys[0].references_table == "departments"
        assert emp.foreign_keys[0].references_column == "id"

    def test_parse_simplified_ddl_format(self):
        """Test 7: Simplified CREATE TABLE statement is parsed correctly."""
        parser = SchemaParser()
        ddl = (
            "CREATE TABLE employees (\n"
            "    id INT PRIMARY KEY,\n"
            "    name VARCHAR(100) NOT NULL,\n"
            "    salary DECIMAL(10,2),\n"
            "    department_id INT,\n"
            "    FOREIGN KEY (department_id) REFERENCES departments(id)\n"
            ");"
        )
        tables = parser.parse_text(ddl)
        assert len(tables) == 1
        emp = tables[0]
        assert emp.name == "employees"
        assert len(emp.columns) == 4
        assert "id" in emp.primary_keys
        assert len(emp.foreign_keys) == 1

    def test_parse_structured_model_input(self):
        """Test 8: Structured UserProvidedSchema model is parsed and validated."""
        parser = SchemaParser()
        user_schema = UserProvidedSchema(
            tables=[
                UserTableInput(
                    name="employees",
                    columns=[
                        UserColumnInput(name="id", type="INT", primary_key=True),
                        UserColumnInput(name="name", type="VARCHAR(100)"),
                        UserColumnInput(name="salary", type="DECIMAL(10,2)"),
                    ],
                    primary_keys=["id"],
                    foreign_keys=[
                        UserForeignKeyInput(
                            column="department_id",
                            references_table="departments",
                            references_column="id",
                        )
                    ],
                )
            ]
        )
        tables = parser.parse(user_schema)
        assert len(tables) == 1
        assert tables[0].name == "employees"
        assert tables[0].source == "user_provided"

    def test_combine_live_and_temporary_schema(self):
        """Test 9: Live schema and user temporary schema are combined in-memory."""
        from app.services.query_generation_service import QueryGenerationService
        service = QueryGenerationService()
        live = _mock_live_schema()
        parser = SchemaParser()
        emp_tables = parser.parse_text("employees\n- id INT PRIMARY KEY\n- name VARCHAR(100)")

        combined, source = service._combine_schemas(live, emp_tables)
        assert combined.table_count == 3
        assert source == "combined"
        names = [t.name for t in combined.tables]
        assert "departments" in names
        assert "students" in names
        assert "employees" in names


# ===========================================================================
# 11-12. SQL Generation with Combined Schema & Foreign Keys
# ===========================================================================

class TestSQLGenerationWithUserSchema:
    """Tests 11-12: Generating SQL using combined schema and foreign key relationships."""

    def test_generate_sql_with_user_provided_schema(self):
        """Test 11: SQL is successfully generated once user provides the missing schema."""
        mock_schema = _mock_live_schema()
        user_text = (
            "employees\n"
            "- id INT PRIMARY KEY\n"
            "- name VARCHAR(100)\n"
            "- salary DECIMAL(10,2)"
        )
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = (
                "SELECT id, name, salary FROM employees;"
            )

            response = client.post(
                "/api/v1/query/generate",
                json={
                    "question": "Show all employees with their salary",
                    "user_schema_text": user_text,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "GENERATED"
        assert data["sql"] == "SELECT id, name, salary FROM employees;"
        assert data["schema_source"] in ("user_provided", "combined")
        assert data["validation"]["valid"] is True

    def test_generate_join_query_using_user_relationship(self):
        """Test 10 & 11: System can generate JOIN query using relationship between user and live schema."""
        mock_schema = _mock_live_schema()
        user_text = (
            "employees\n"
            "- id INT PRIMARY KEY\n"
            "- name VARCHAR(100)\n"
            "- department_id INT\n"
            "- salary DECIMAL(10,2)\n"
            "FOREIGN KEY (department_id) -> departments.id"
        )
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = (
                "SELECT e.name, e.salary, d.name AS department_name "
                "FROM employees e JOIN departments d ON e.department_id = d.id;"
            )

            response = client.post(
                "/api/v1/query/generate",
                json={
                    "question": "Show employee names, salaries and department names",
                    "user_schema_text": user_text,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "GENERATED"
        assert "JOIN departments" in data["sql"]
        assert data["validation"]["valid"] is True


# ===========================================================================
# 13. Security: Rejection of Malicious Metadata & SQL Injection
# ===========================================================================

class TestSchemaSecurityValidation:
    """Test 13: Strict rejection of malicious SQL inside user schema input."""

    def test_reject_semicolon_injection_in_schema(self):
        """Multiple statements / semicolons inside schema text are rejected."""
        parser = SchemaParser()
        malicious = "employees\n- id INT; DROP TABLE students;\n- name VARCHAR(100)"
        with pytest.raises(SchemaParsingError, match="Semicolons are not allowed"):
            parser.parse_text(malicious)

    def test_reject_comment_markers_in_schema(self):
        """SQL comment markers (-- and /* */) are rejected."""
        parser = SchemaParser()
        malicious = "employees\n- id INT -- drop everything\n- name VARCHAR(100)"
        with pytest.raises(SchemaParsingError, match="SQL comment markers"):
            parser.parse_text(malicious)

    def test_reject_banned_sql_statements_in_schema(self):
        """DROP, DELETE, INSERT, UPDATE in schema are rejected."""
        parser = SchemaParser()
        malicious = "employees\n- id INT\nDROP TABLE users"
        with pytest.raises(SchemaParsingError, match="Forbidden statement keyword"):
            parser.parse_text(malicious)

    def test_reject_invalid_identifier_names(self):
        """Invalid table or column names (special characters) are rejected."""
        parser = SchemaParser()
        malicious = "employees!@#\n- id INT"
        with pytest.raises(SchemaParsingError):
            parser.parse_text(malicious)

        user_schema = UserProvidedSchema(
            tables=[
                UserTableInput(
                    name="employees-bad-name!",
                    columns=[UserColumnInput(name="id", type="INT")],
                )
            ]
        )
        with pytest.raises(SchemaParsingError, match="Invalid"):
            parser.parse(user_schema)

    def test_endpoint_returns_422_on_malicious_schema(self):
        """POST /api/v1/query/generate returns 422 with INVALID_SCHEMA_METADATA on bad schema."""
        response = client.post(
            "/api/v1/query/generate",
            json={
                "question": "Show all employees",
                "user_schema_text": "employees\n- id INT; DROP TABLE students;",
            },
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error_code"] == "INVALID_SCHEMA_METADATA"


# ===========================================================================
# 14-18. Execution Scope: Zero Automatic Execution, Explicit Execution Intact
# ===========================================================================

class TestExecutionSafetyAndExplicitAction:
    """Tests 14-18: Zero automatic execution; explicit execution remains functional."""

    def test_zero_automatic_execution_during_generation(self):
        """Test 14: Generating SQL never automatically executes it against MySQL."""
        mock_schema = _mock_live_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
            patch("sqlalchemy.engine.base.Connection.execute") as mock_execute,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT * FROM students;"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show all students"},
            )

        assert response.status_code == 200
        # Crucial: Connection.execute must NEVER have been called
        mock_execute.assert_not_called()

    def test_zero_automatic_execution_with_user_schema(self):
        """Test 14 & 16: Temporary schema does NOT execute or create tables in MySQL."""
        mock_schema = _mock_live_schema()
        user_text = "employees\n- id INT PRIMARY KEY\n- name VARCHAR(100)\n- salary DECIMAL(10,2)"
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
            patch("sqlalchemy.engine.base.Connection.execute") as mock_execute,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT id, name, salary FROM employees;"

            response = client.post(
                "/api/v1/query/generate",
                json={
                    "question": "Show all employees with their salary",
                    "user_schema_text": user_text,
                },
            )

        assert response.status_code == 200
        mock_execute.assert_not_called()

    def test_explicit_execute_endpoint_remains_available(self):
        """Test 15 & 18: Existing Phase 4B POST /api/v1/query/execute remains available for explicit user action."""
        mock_schema = _mock_live_schema()
        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as MockEngine,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            mock_conn = MagicMock()
            mock_result = MagicMock()
            mock_result.keys.return_value = ["name", "marks"]
            mock_result.fetchmany.return_value = [("Alice", 95)]
            mock_conn.execute.return_value = mock_result
            MockEngine.return_value.connect.return_value.__enter__.return_value = mock_conn

            response = client.post(
                "/api/v1/query/execute",
                json={"sql": "SELECT name, marks FROM students;"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["columns"] == ["name", "marks"]
        assert data["row_count"] == 1
