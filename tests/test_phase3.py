"""
Phase 3 tests: Gemini-Based NL-to-SQL Generation.

Tests cover:
  1.  QueryGenerationRequest accepts valid question.
  2.  Empty question is rejected.
  3.  Whitespace-only question is rejected.
  4.  QueryGenerationResponse model works correctly.
  5.  Gemini configuration fields load correctly.
  6.  GEMINI_API_KEY setting exists in Settings.
  7.  API key is never exposed in responses.
  8.  Schema is retrieved before generation (SchemaService called).
  9.  Schema context is included in the prompt.
  10. MySQL dialect instruction is included in the prompt.
  11. Prompt instructs model to use only available schema.
  12. Prompt instructs model to generate SELECT only.
  13. GeminiService can be mocked.
  14. Successful Gemini response produces SQL.
  15. Markdown SQL fences are stripped.
  16. Empty Gemini response raises GeminiServiceError.
  17. Gemini API error is handled safely.
  18. Rate-limit/client error is handled safely.
  19. POST /api/v1/query/generate returns 200 with SQL.
  20. Request ID is included in the response.
  21. Generated SQL is NOT executed (no execute() calls).
  22. Existing Phase 1 tests remain unaffected (verified via import).
  23. Existing Phase 2 tests remain unaffected (verified via import).

All Gemini API calls are mocked — tests do NOT make real network requests.
"""

import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.main import app
from app.models.query_models import QueryGenerationRequest, QueryGenerationResponse
from app.models.schema_models import (
    ColumnSchema,
    DatabaseSchema,
    ForeignKeySchema,
    TableSchema,
    UniqueConstraintSchema,
)
from app.services.gemini_service import GeminiService, GeminiServiceError
from app.services.prompt_builder import PromptBuilder

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers — mock schema (uses minor_project; no hardcoded real table names)
# ---------------------------------------------------------------------------

def _mock_schema() -> DatabaseSchema:
    """Return a realistic mock DatabaseSchema for Phase 3 tests."""
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
        unique_constraints=[
            UniqueConstraintSchema(name="uq_email", columns=["email"]),
        ],
    )
    departments = TableSchema(
        name="departments",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="name", type="VARCHAR(100)", nullable=False),
        ],
        primary_keys=["id"],
        foreign_keys=[],
        unique_constraints=[
            UniqueConstraintSchema(name="uq_dept_name", columns=["name"]),
        ],
    )
    return DatabaseSchema(
        database="minor_project",
        tables=[departments, students],
        table_count=2,
    )


def _mock_gemini_service(sql: str = "SELECT * FROM students;") -> MagicMock:
    """Return a mock GeminiService that returns a fixed SQL string."""
    mock = MagicMock(spec=GeminiService)
    mock.generate_sql.return_value = sql
    return mock


# ===========================================================================
# 1-3. QueryGenerationRequest validation
# ===========================================================================

class TestQueryGenerationRequest:
    """Test 1-3: Request model validation."""

    def test_accepts_valid_question(self):
        """Test 1: A non-empty question string should be accepted."""
        req = QueryGenerationRequest(question="Show all students")
        assert req.question == "Show all students"

    def test_empty_question_is_rejected(self):
        """Test 2: An empty string must raise a ValidationError."""
        with pytest.raises(ValidationError):
            QueryGenerationRequest(question="")

    def test_whitespace_only_question_is_rejected(self):
        """Test 3: A whitespace-only question must raise a ValidationError."""
        with pytest.raises(ValidationError):
            QueryGenerationRequest(question="   ")

    def test_question_is_stripped_of_leading_trailing_whitespace(self):
        """Leading/trailing whitespace on a valid question is stripped."""
        req = QueryGenerationRequest(question="  Show all students  ")
        assert req.question == "Show all students"

    def test_tab_only_question_is_rejected(self):
        """A tab-only question must raise a ValidationError."""
        with pytest.raises(ValidationError):
            QueryGenerationRequest(question="\t\n")


# ===========================================================================
# 4. QueryGenerationResponse model
# ===========================================================================

class TestQueryGenerationResponse:
    """Test 4: Response model serialization."""

    def test_response_model_serializes_correctly(self):
        """Test 4: QueryGenerationResponse should expose all required fields."""
        resp = QueryGenerationResponse(
            question="Show all students",
            sql="SELECT * FROM students;",
            model="gemini-3.6-flash",
            request_id="req-123",
        )
        data = resp.model_dump()
        assert data["question"] == "Show all students"
        assert data["sql"] == "SELECT * FROM students;"
        assert data["model"] == "gemini-3.6-flash"
        assert data["request_id"] == "req-123"

    def test_response_model_request_id_is_optional(self):
        """request_id should default to None."""
        resp = QueryGenerationResponse(
            question="Show all students",
            sql="SELECT * FROM students;",
            model="gemini-3.6-flash",
        )
        assert resp.request_id is None

    def test_response_does_not_have_api_key_field(self):
        """Test 7 (partial): The response model must not have an api_key field."""
        resp = QueryGenerationResponse(
            question="q",
            sql="SELECT 1;",
            model="gemini-3.6-flash",
        )
        data = resp.model_dump()
        assert "api_key" not in data
        assert "gemini_api_key" not in data
        assert "GEMINI_API_KEY" not in data


# ===========================================================================
# 5-6. Gemini configuration
# ===========================================================================

class TestGeminiConfiguration:
    """Tests 5-6: Settings contain Gemini configuration."""

    def test_settings_has_gemini_api_key_field(self):
        """Test 5-6: Settings must have a GEMINI_API_KEY attribute."""
        assert hasattr(settings, "GEMINI_API_KEY")

    def test_gemini_api_key_is_a_string(self):
        """Test 6: GEMINI_API_KEY must be a string type."""
        assert isinstance(settings.GEMINI_API_KEY, str)

    def test_settings_has_gemini_model_field(self):
        """Test 5: Settings must have a GEMINI_MODEL attribute."""
        assert hasattr(settings, "GEMINI_MODEL")

    def test_gemini_model_is_a_string(self):
        """Test 5: GEMINI_MODEL must be a non-empty string."""
        assert isinstance(settings.GEMINI_MODEL, str)
        assert len(settings.GEMINI_MODEL) > 0

    def test_gemini_model_default_is_flash(self):
        """Default model should reference a Gemini Flash variant."""
        assert "gemini" in settings.GEMINI_MODEL.lower()
        assert "flash" in settings.GEMINI_MODEL.lower()


# ===========================================================================
# 7. API key security
# ===========================================================================

class TestAPIKeySecurity:
    """Test 7: API key must never appear in responses."""

    def test_api_key_not_in_success_response(self):
        """Test 7: A successful generation response must not contain the API key."""
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
        response_text = response.text
        # The real API key value should never appear in responses.
        # We also check for the placeholder sentinel.
        assert "your_gemini_api_key_here" not in response_text
        # Generic check: no field called api_key in the response JSON
        data = response.json()
        assert "api_key" not in data
        assert "GEMINI_API_KEY" not in data

    def test_api_key_not_in_error_response(self):
        """Test 7: An error response must not contain the API key."""
        with patch("app.services.query_generation_service.GeminiService") as MockGemini:
            MockGemini.side_effect = GeminiServiceError("Gemini API key is not configured.")
            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show all students"},
            )
        # Error response should not expose the actual key value
        response_text = response.text
        assert "your_gemini_api_key_here" not in response_text


# ===========================================================================
# 8-12. Prompt and schema tests
# ===========================================================================

class TestPromptBuilder:
    """Tests 8-12: Schema retrieval and prompt composition."""

    def test_schema_context_is_included_in_prompt(self):
        """Test 9: The generated prompt must contain database/table names from schema."""
        schema = _mock_schema()
        builder = PromptBuilder()
        prompt = builder.build_prompt(schema, "Show all students")
        assert "minor_project" in prompt
        assert "students" in prompt
        assert "departments" in prompt

    def test_column_names_appear_in_prompt(self):
        """Test 9: Column names from the schema should be in the prompt."""
        schema = _mock_schema()
        builder = PromptBuilder()
        prompt = builder.build_prompt(schema, "Show all students")
        assert "name" in prompt
        assert "marks" in prompt
        assert "department_id" in prompt

    def test_foreign_keys_appear_in_prompt(self):
        """Test 9: Foreign key relationships should be included in the prompt."""
        schema = _mock_schema()
        builder = PromptBuilder()
        prompt = builder.build_prompt(schema, "Show all students")
        # FK from students.department_id -> departments.id
        assert "departments" in prompt

    def test_mysql_dialect_in_prompt(self):
        """Test 10: The prompt must mention MySQL dialect explicitly."""
        schema = _mock_schema()
        builder = PromptBuilder()
        prompt = builder.build_prompt(schema, "Show all students")
        assert "MySQL" in prompt or "mysql" in prompt.lower()

    def test_prompt_instructs_select_only(self):
        """Test 12: The prompt must forbid INSERT, UPDATE, DELETE, DROP etc."""
        schema = _mock_schema()
        builder = PromptBuilder()
        prompt = builder.build_prompt(schema, "Show all students")
        assert "SELECT" in prompt
        assert "INSERT" in prompt   # mentioned as forbidden
        assert "DELETE" in prompt   # mentioned as forbidden
        assert "DROP" in prompt     # mentioned as forbidden

    def test_prompt_instructs_use_only_provided_schema(self):
        """Test 11: Prompt must instruct Gemini to use only provided schema."""
        schema = _mock_schema()
        builder = PromptBuilder()
        prompt = builder.build_prompt(schema, "Show all students")
        # Should contain instruction about not inventing tables/columns
        prompt_lower = prompt.lower()
        assert "only" in prompt_lower
        # Should contain the database schema section
        assert "Database Schema" in prompt or "database schema" in prompt_lower

    def test_prompt_includes_user_question(self):
        """The user question must appear verbatim in the assembled prompt."""
        schema = _mock_schema()
        builder = PromptBuilder()
        question = "Show students with marks above 90"
        prompt = builder.build_prompt(schema, question)
        assert question in prompt

    def test_schema_is_retrieved_before_generation(self):
        """Test 8: SchemaService.get_full_schema() must be called during generation."""
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
        MockSchema.return_value.get_full_schema.assert_called_once()


# ===========================================================================
# 13-18. GeminiService unit tests
# ===========================================================================

class TestGeminiService:
    """Tests 13-18: GeminiService behaviour with mocked Gemini SDK."""

    def test_gemini_service_can_be_mocked(self):
        """Test 13: GeminiService can be replaced with a MagicMock in tests."""
        mock_service = _mock_gemini_service("SELECT id FROM students;")
        result = mock_service.generate_sql("any prompt")
        assert result == "SELECT id FROM students;"
        mock_service.generate_sql.assert_called_once_with("any prompt")

    def test_successful_response_produces_sql(self):
        """Test 14: A successful Gemini response should return a SQL string."""
        expected_sql = "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;"
        with patch("app.services.gemini_service.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_response = MagicMock()
            mock_response.text = expected_sql
            mock_client.models.generate_content.return_value = mock_response

            with patch.object(settings, "GEMINI_API_KEY", "fake-key-for-test"):
                service = GeminiService()
                result = service.generate_sql("any prompt")

        assert result == expected_sql

    def test_markdown_sql_fences_are_stripped(self):
        """Test 15: Markdown ```sql ... ``` fences must be removed from the output."""
        raw_response = "```sql\nSELECT * FROM students;\n```"
        with patch("app.services.gemini_service.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_response = MagicMock()
            mock_response.text = raw_response
            mock_client.models.generate_content.return_value = mock_response

            with patch.object(settings, "GEMINI_API_KEY", "fake-key-for-test"):
                service = GeminiService()
                result = service.generate_sql("any prompt")

        assert result == "SELECT * FROM students;"
        assert "```" not in result

    def test_plain_code_fences_are_stripped(self):
        """Test 15 (variant): Plain ``` fences without language identifier are stripped."""
        raw_response = "```\nSELECT * FROM students;\n```"
        with patch("app.services.gemini_service.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_response = MagicMock()
            mock_response.text = raw_response
            mock_client.models.generate_content.return_value = mock_response

            with patch.object(settings, "GEMINI_API_KEY", "fake-key-for-test"):
                service = GeminiService()
                result = service.generate_sql("any prompt")

        assert result == "SELECT * FROM students;"

    def test_empty_gemini_response_raises_error(self):
        """Test 16: An empty Gemini response must raise GeminiServiceError."""
        with patch("app.services.gemini_service.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_response = MagicMock()
            mock_response.text = ""
            mock_client.models.generate_content.return_value = mock_response

            with patch.object(settings, "GEMINI_API_KEY", "fake-key-for-test"):
                service = GeminiService()
                with pytest.raises(GeminiServiceError):
                    service.generate_sql("any prompt")

    def test_whitespace_only_gemini_response_raises_error(self):
        """Test 16 (variant): Whitespace-only response raises GeminiServiceError."""
        with patch("app.services.gemini_service.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_response = MagicMock()
            mock_response.text = "   \n  "
            mock_client.models.generate_content.return_value = mock_response

            with patch.object(settings, "GEMINI_API_KEY", "fake-key-for-test"):
                service = GeminiService()
                with pytest.raises(GeminiServiceError):
                    service.generate_sql("any prompt")

    def test_gemini_api_error_is_handled_safely(self):
        """Test 17: A generic exception from the SDK raises GeminiServiceError."""
        with patch("app.services.gemini_service.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_client.models.generate_content.side_effect = Exception("network failure")

            with patch.object(settings, "GEMINI_API_KEY", "fake-key-for-test"):
                service = GeminiService()
                with pytest.raises(GeminiServiceError) as exc_info:
                    service.generate_sql("any prompt")

        # The error message must be safe (no raw exception details / no API key)
        assert "network failure" not in str(exc_info.value)

    def test_rate_limit_error_is_handled_safely(self):
        """Test 18: A ClientError (quota/rate-limit) raises GeminiServiceError."""
        from google.genai import errors as genai_errors

        with patch("app.services.gemini_service.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            # Simulate a rate-limit ClientError
            mock_client.models.generate_content.side_effect = (
                genai_errors.ClientError(429, {"error": {"message": "rate limit exceeded"}})
            )
            mock_genai.errors = genai_errors

            with patch.object(settings, "GEMINI_API_KEY", "fake-key-for-test"):
                service = GeminiService()
                with pytest.raises(GeminiServiceError):
                    service.generate_sql("any prompt")

    def test_missing_api_key_raises_error(self):
        """GeminiService should raise GeminiServiceError when key is missing."""
        with patch.object(settings, "GEMINI_API_KEY", ""):
            with pytest.raises(GeminiServiceError) as exc_info:
                GeminiService()
        assert "API key" in str(exc_info.value)

    def test_placeholder_api_key_raises_error(self):
        """GeminiService should raise GeminiServiceError when key is still placeholder."""
        with patch.object(settings, "GEMINI_API_KEY", "your_gemini_api_key_here"):
            with pytest.raises(GeminiServiceError):
                GeminiService()


# ===========================================================================
# 19-21. POST /api/v1/query/generate endpoint tests
# ===========================================================================

class TestQueryGenerateEndpoint:
    """Tests 19-21: API endpoint behaviour."""

    def test_endpoint_returns_200_with_sql(self):
        """Test 19: A valid question should return 200 with a sql field."""
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT name FROM students WHERE marks > 80;"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show students who scored above 80"},
            )

        assert response.status_code == 200
        data = response.json()
        assert "sql" in data
        assert data["sql"] == "SELECT name FROM students WHERE marks > 80;"
        assert data["question"] == "Show students who scored above 80"

    def test_response_includes_request_id(self):
        """Test 20: The response body must include a request_id field."""
        mock_schema = _mock_schema()
        custom_id = "phase3-trace-abc"
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT * FROM students;"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "List all students"},
                headers={"X-Request-ID": custom_id},
            )

        assert response.status_code == 200
        data = response.json()
        assert data.get("request_id") == custom_id

    def test_generated_sql_is_not_executed(self):
        """Test 21: The route must not call any SQL execute() method."""
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
            patch("sqlalchemy.engine.base.Connection.execute") as mock_execute,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT * FROM students;"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "List all students"},
            )

        assert response.status_code == 200
        # The SQLAlchemy execute() must NOT have been called with the generated SQL
        mock_execute.assert_not_called()

    def test_empty_question_returns_422(self):
        """An empty question must return 422 Unprocessable Entity."""
        response = client.post(
            "/api/v1/query/generate",
            json={"question": ""},
        )
        assert response.status_code == 422

    def test_whitespace_question_returns_422(self):
        """A whitespace-only question must return 422 Unprocessable Entity."""
        response = client.post(
            "/api/v1/query/generate",
            json={"question": "   "},
        )
        assert response.status_code == 422

    def test_missing_question_field_returns_422(self):
        """A request body without a question field must return 422."""
        response = client.post(
            "/api/v1/query/generate",
            json={},
        )
        assert response.status_code == 422

    def test_endpoint_registered_in_openapi(self):
        """POST /api/v1/query/generate must appear in the OpenAPI spec."""
        paths = list(app.openapi()["paths"].keys())
        assert "/api/v1/query/generate" in paths

    def test_schema_retrieval_failure_returns_500(self):
        """A SQLAlchemyError during schema retrieval must return 500."""
        from sqlalchemy.exc import SQLAlchemyError
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService"),
        ):
            MockSchema.return_value.get_full_schema.side_effect = SQLAlchemyError("db down")
            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show all students"},
            )
        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["error_code"] == "SCHEMA_RETRIEVAL_FAILED"
        assert "request_id" in detail

    def test_gemini_error_returns_503(self):
        """A GeminiServiceError must return 503 with SQL_GENERATION_FAILED."""
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.side_effect = GeminiServiceError(
                "Gemini API is temporarily unavailable."
            )
            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show all students"},
            )
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["error_code"] == "SQL_GENERATION_FAILED"
        assert "request_id" in detail

    def test_gemini_error_message_is_safe(self):
        """Gemini error responses must not expose internal details or API keys."""
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.side_effect = GeminiServiceError(
                "Gemini API is temporarily unavailable."
            )
            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show all students"},
            )
        response_text = response.text
        assert "your_gemini_api_key_here" not in response_text

    def test_response_contains_model_field(self):
        """The response must include the model name used."""
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT * FROM students;"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "List all students"},
            )
        assert response.status_code == 200
        data = response.json()
        assert "model" in data
        assert len(data["model"]) > 0

    def test_x_request_id_header_in_response(self):
        """X-Request-ID header must be present in all responses."""
        mock_schema = _mock_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = mock_schema
            MockGemini.return_value.generate_sql.return_value = "SELECT * FROM students;"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "List all students"},
            )
        assert "x-request-id" in response.headers


# ===========================================================================
# 22-23. Verify existing Phase 1 & 2 tests are unaffected
# ===========================================================================

class TestExistingPhasesUnaffected:
    """Tests 22-23: Existing Phase 1 and Phase 2 tests continue to pass."""

    def test_phase1_schema_endpoint_still_works(self):
        """Test 22: GET /schema (Phase 1 legacy) must still return 200."""
        from app.models.schema_models import DatabaseSchema
        mock_schema = _mock_schema()
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_database_schema.return_value = mock_schema
            response = client.get("/schema")
        assert response.status_code == 200

    def test_phase2_health_endpoint_still_works(self):
        """Test 23: GET /api/v1/health (Phase 2) must still return 200."""
        with patch("app.api.v1.health_routes.verify_connection", return_value=True):
            response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_phase2_schema_endpoint_still_works(self):
        """Test 23: GET /api/v1/schema (Phase 2) must still return 200."""
        mock_schema = _mock_schema()
        with patch("app.api.v1.schema_routes.SchemaService") as MockService:
            MockService.return_value.get_full_schema.return_value = mock_schema
            response = client.get("/api/v1/schema")
        assert response.status_code == 200

    def test_all_v1_routes_registered(self):
        """All Phase 2 and Phase 3 routes must appear in the OpenAPI spec."""
        paths = list(app.openapi()["paths"].keys())
        assert "/api/v1/health" in paths
        assert "/api/v1/schema" in paths
        assert "/api/v1/schema/{table_name}" in paths
        assert "/api/v1/query/generate" in paths
