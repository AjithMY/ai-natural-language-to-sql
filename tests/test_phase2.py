"""
Phase 2 tests: Backend Foundation and API Architecture.

Tests cover:
  - Configuration loading (CORS_ORIGINS, ENVIRONMENT, APP_DEBUG)
  - Response models (HealthResponse, ErrorResponse)
  - SchemaService (service layer)
  - GET /api/v1/health
  - GET /api/v1/schema
  - GET /api/v1/schema/{table_name}
  - API versioning (v1 routes exist; legacy routes still work)
  - CORS configuration (correct headers for allowed/unknown origins)
  - Request ID (X-Request-ID header on all responses; custom ID echoed; in body)
  - Global error handling (unhandled exceptions → safe 500 with error_code)
  - Legacy endpoints still functional after Phase 2 changes
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from app.core.config import settings
from app.models.response_models import ErrorResponse, HealthResponse
from app.models.schema_models import (
    ColumnSchema,
    DatabaseSchema,
    ForeignKeySchema,
    TableSchema,
    UniqueConstraintSchema,
)
from app.services.schema_service import SchemaService

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers (mirrors what test_schema.py uses — kept local to avoid coupling)
# ---------------------------------------------------------------------------

def _mock_database_schema() -> DatabaseSchema:
    """Return a minimal mock DatabaseSchema for Phase 2 tests."""
    students = TableSchema(
        name="students",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="name", type="VARCHAR(100)", nullable=False),
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
        unique_constraints=[],
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
        database="college_db",
        tables=[departments, students],
        table_count=2,
    )


def _mock_students_table() -> TableSchema:
    return next(t for t in _mock_database_schema().tables if t.name == "students")


# ===========================================================================
# 1. Configuration Tests
# ===========================================================================

class TestAppConfig:
    """Verify Phase 2 configuration fields are present and have correct types."""

    def test_settings_has_cors_origins(self):
        """CORS_ORIGINS must be a non-empty list."""
        assert hasattr(settings, "CORS_ORIGINS")
        assert isinstance(settings.CORS_ORIGINS, list)
        assert len(settings.CORS_ORIGINS) > 0

    def test_cors_origins_are_strings(self):
        """Each entry in CORS_ORIGINS must be a string."""
        for origin in settings.CORS_ORIGINS:
            assert isinstance(origin, str)

    def test_settings_has_environment(self):
        """ENVIRONMENT must be a non-empty string."""
        assert hasattr(settings, "ENVIRONMENT")
        assert isinstance(settings.ENVIRONMENT, str)
        assert settings.ENVIRONMENT != ""

    def test_settings_has_app_debug(self):
        """APP_DEBUG must be a boolean."""
        assert hasattr(settings, "APP_DEBUG")
        assert isinstance(settings.APP_DEBUG, bool)

    def test_database_url_contains_host_and_db(self):
        """database_url must include DB_HOST and DB_NAME (but not be tested for password)."""
        url = settings.database_url
        assert settings.DB_HOST in url
        assert settings.DB_NAME in url


# ===========================================================================
# 2. Response Model Tests
# ===========================================================================

class TestResponseModels:
    """Verify HealthResponse and ErrorResponse serialize correctly."""

    def test_health_response_with_request_id(self):
        """HealthResponse should expose status, database, request_id."""
        resp = HealthResponse(status="ok", database="connected", request_id="req-123")
        data = resp.model_dump()
        assert data["status"] == "ok"
        assert data["database"] == "connected"
        assert data["request_id"] == "req-123"

    def test_health_response_request_id_optional(self):
        """HealthResponse request_id should default to None."""
        resp = HealthResponse(status="ok", database="connected")
        assert resp.request_id is None

    def test_error_response_fields(self):
        """ErrorResponse should expose error_code, message, request_id."""
        resp = ErrorResponse(
            error_code="TABLE_NOT_FOUND",
            message="Table 'x' does not exist.",
            request_id="req-456",
        )
        data = resp.model_dump()
        assert data["error_code"] == "TABLE_NOT_FOUND"
        assert data["message"] == "Table 'x' does not exist."
        assert data["request_id"] == "req-456"

    def test_error_response_request_id_optional(self):
        """ErrorResponse request_id should default to None."""
        resp = ErrorResponse(error_code="INTERNAL_ERROR", message="Something went wrong.")
        assert resp.request_id is None


# ===========================================================================
# 3. SchemaService Tests
# ===========================================================================

class TestSchemaService:
    """Verify the service layer correctly delegates to SchemaManager."""

    def test_get_full_schema_delegates_to_manager(self):
        """SchemaService.get_full_schema() should call SchemaManager.get_database_schema()."""
        mock_schema = _mock_database_schema()
        with patch("app.services.schema_service.SchemaManager") as MockManager:
            MockManager.return_value.get_database_schema.return_value = mock_schema
            service = SchemaService()
            result = service.get_full_schema()
        assert result.database == "college_db"
        MockManager.return_value.get_database_schema.assert_called_once()

    def test_get_table_schema_delegates_to_manager(self):
        """SchemaService.get_table_schema() should call SchemaManager.get_table_schema()."""
        mock_table = _mock_students_table()
        with patch("app.services.schema_service.SchemaManager") as MockManager:
            MockManager.return_value.get_table_schema.return_value = mock_table
            service = SchemaService()
            result = service.get_table_schema("students")
        assert result.name == "students"
        MockManager.return_value.get_table_schema.assert_called_once_with("students")

    def test_get_table_names_delegates_to_manager(self):
        """SchemaService.get_table_names() should call SchemaManager.get_table_names()."""
        with patch("app.services.schema_service.SchemaManager") as MockManager:
            MockManager.return_value.get_table_names.return_value = ["students", "departments"]
            service = SchemaService()
            result = service.get_table_names()
        assert "students" in result
        assert "departments" in result


# ===========================================================================
# 4. GET /api/v1/health
# ===========================================================================

class TestV1HealthEndpoint:
    """Tests for GET /api/v1/health."""

    def test_returns_200_when_db_connected(self):
        """Should return 200 with status=ok and database=connected."""
        with patch("app.api.v1.health_routes.verify_connection", return_value=True):
            response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "connected"

    def test_response_includes_request_id_field(self):
        """Response body should contain a request_id field."""
        with patch("app.api.v1.health_routes.verify_connection", return_value=True):
            response = client.get("/api/v1/health")
        data = response.json()
        assert "request_id" in data
        assert data["request_id"] is not None

    def test_returns_503_when_db_disconnected(self):
        """Should return 503 with error_code=DATABASE_UNAVAILABLE in detail."""
        with patch("app.api.v1.health_routes.verify_connection", return_value=False):
            response = client.get("/api/v1/health")
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["error_code"] == "DATABASE_UNAVAILABLE"
        assert "request_id" in detail

    def test_503_detail_message_is_safe(self):
        """503 detail message must not expose internal connection strings."""
        with patch("app.api.v1.health_routes.verify_connection", return_value=False):
            response = client.get("/api/v1/health")
        detail = response.json()["detail"]
        assert "pymysql" not in detail["message"].lower()
        assert "password" not in detail["message"].lower()


# ===========================================================================
# 5. GET /api/v1/schema
# ===========================================================================

class TestV1SchemaEndpoint:
    """Tests for GET /api/v1/schema."""

    def test_returns_200_with_valid_schema(self):
        """Should return 200 with the full DatabaseSchema."""
        mock_schema = _mock_database_schema()
        with patch("app.api.v1.schema_routes.SchemaService") as MockService:
            MockService.return_value.get_full_schema.return_value = mock_schema
            response = client.get("/api/v1/schema")
        assert response.status_code == 200
        data = response.json()
        assert data["database"] == "college_db"
        assert data["table_count"] == 2

    def test_returns_500_on_db_error(self):
        """Should return 500 with error_code=SCHEMA_RETRIEVAL_FAILED on SQLAlchemyError."""
        from sqlalchemy.exc import SQLAlchemyError
        with patch("app.api.v1.schema_routes.SchemaService") as MockService:
            MockService.return_value.get_full_schema.side_effect = SQLAlchemyError("db down")
            response = client.get("/api/v1/schema")
        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["error_code"] == "SCHEMA_RETRIEVAL_FAILED"
        assert "request_id" in detail

    def test_500_message_does_not_expose_internal_details(self):
        """Internal exception message must not appear in the client response."""
        from sqlalchemy.exc import SQLAlchemyError
        with patch("app.api.v1.schema_routes.SchemaService") as MockService:
            MockService.return_value.get_full_schema.side_effect = SQLAlchemyError(
                "super secret internal db error detail"
            )
            response = client.get("/api/v1/schema")
        detail = response.json()["detail"]
        assert "super secret internal db error detail" not in detail["message"]


# ===========================================================================
# 6. GET /api/v1/schema/{table_name}
# ===========================================================================

class TestV1TableSchemaEndpoint:
    """Tests for GET /api/v1/schema/{table_name}."""

    def test_existing_table_returns_200(self):
        """Valid table name should return 200 with TableSchema."""
        mock_table = _mock_students_table()
        with patch("app.api.v1.schema_routes.SchemaService") as MockService:
            MockService.return_value.get_table_schema.return_value = mock_table
            response = client.get("/api/v1/schema/students")
        assert response.status_code == 200
        assert response.json()["name"] == "students"

    def test_nonexistent_table_returns_404_with_error_code(self):
        """Non-existent table should return 404 with error_code=TABLE_NOT_FOUND."""
        with patch("app.api.v1.schema_routes.SchemaService") as MockService:
            MockService.return_value.get_table_schema.side_effect = ValueError(
                "Table 'ghost' does not exist"
            )
            response = client.get("/api/v1/schema/ghost")
        assert response.status_code == 404
        detail = response.json()["detail"]
        assert detail["error_code"] == "TABLE_NOT_FOUND"
        assert "request_id" in detail

    def test_db_error_returns_500_with_error_code(self):
        """SQLAlchemyError should return 500 with error_code=SCHEMA_RETRIEVAL_FAILED."""
        from sqlalchemy.exc import SQLAlchemyError
        with patch("app.api.v1.schema_routes.SchemaService") as MockService:
            MockService.return_value.get_table_schema.side_effect = SQLAlchemyError("mock")
            response = client.get("/api/v1/schema/students")
        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["error_code"] == "SCHEMA_RETRIEVAL_FAILED"


# ===========================================================================
# 7. API Versioning
# ===========================================================================

class TestAPIVersioning:
    """Verify versioned routes exist and legacy routes still function."""

    def test_v1_routes_are_registered_in_openapi(self):
        """OpenAPI spec must include all three /api/v1/* paths."""
        paths = list(app.openapi()["paths"].keys())
        assert "/api/v1/health" in paths
        assert "/api/v1/schema" in paths
        assert "/api/v1/schema/{table_name}" in paths

    def test_legacy_routes_still_in_openapi(self):
        """Phase 1 routes must still appear in OpenAPI spec."""
        paths = list(app.openapi()["paths"].keys())
        assert "/health" in paths
        assert "/schema" in paths
        assert "/schema/{table_name}" in paths

    def test_legacy_health_still_returns_200(self):
        """GET /health (Phase 1) must still return 200 after Phase 2 changes."""
        with patch("app.api.schema_routes.verify_connection", return_value=True):
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_legacy_schema_still_returns_200(self):
        """GET /schema (Phase 1) must still return 200 after Phase 2 changes."""
        mock_schema = _mock_database_schema()
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_database_schema.return_value = mock_schema
            response = client.get("/schema")
        assert response.status_code == 200
        assert response.json()["database"] == "college_db"

    def test_unknown_v1_path_returns_404(self):
        """An unregistered /api/v1/* path should return 404."""
        response = client.get("/api/v1/nonexistent-route")
        assert response.status_code == 404


# ===========================================================================
# 8. CORS Configuration
# ===========================================================================

class TestCORSConfiguration:
    """Verify CORS headers are set correctly for allowed and unknown origins."""

    def test_allowed_origin_receives_cors_header(self):
        """A request from an allowed origin should receive Access-Control-Allow-Origin."""
        allowed_origin = settings.CORS_ORIGINS[0]
        with patch("app.api.schema_routes.verify_connection", return_value=True):
            response = client.get("/health", headers={"Origin": allowed_origin})
        assert "access-control-allow-origin" in response.headers
        assert response.headers["access-control-allow-origin"] == allowed_origin

    def test_unknown_origin_does_not_receive_cors_header(self):
        """A request from an unknown origin should NOT receive Access-Control-Allow-Origin."""
        with patch("app.api.schema_routes.verify_connection", return_value=True):
            response = client.get(
                "/health", headers={"Origin": "http://definitely-not-allowed.example.com"}
            )
        assert response.headers.get("access-control-allow-origin") is None


# ===========================================================================
# 9. Request ID
# ===========================================================================

class TestRequestID:
    """Verify X-Request-ID behaviour."""

    def test_every_response_has_x_request_id_header(self):
        """X-Request-ID must be present in every response header."""
        with patch("app.api.schema_routes.verify_connection", return_value=True):
            response = client.get("/health")
        assert "x-request-id" in response.headers

    def test_client_supplied_request_id_is_echoed(self):
        """A client-provided X-Request-ID must be returned unchanged in the response."""
        custom_id = "my-trace-id-9999"
        with patch("app.api.schema_routes.verify_connection", return_value=True):
            response = client.get("/health", headers={"X-Request-ID": custom_id})
        assert response.headers.get("x-request-id") == custom_id

    def test_server_generates_request_id_when_not_provided(self):
        """When no X-Request-ID is sent, the server should generate one (non-empty)."""
        with patch("app.api.schema_routes.verify_connection", return_value=True):
            response = client.get("/health")
        rid = response.headers.get("x-request-id", "")
        assert len(rid) > 0

    def test_v1_health_response_body_contains_request_id(self):
        """The v1 /health response body should embed the same request_id as the header."""
        custom_id = "correlation-id-abc"
        with patch("app.api.v1.health_routes.verify_connection", return_value=True):
            response = client.get(
                "/api/v1/health", headers={"X-Request-ID": custom_id}
            )
        data = response.json()
        assert data.get("request_id") == custom_id

    def test_v1_error_response_contains_request_id(self):
        """A v1 error response should embed request_id in the detail body."""
        custom_id = "err-correlation-xyz"
        with patch("app.api.v1.health_routes.verify_connection", return_value=False):
            response = client.get(
                "/api/v1/health", headers={"X-Request-ID": custom_id}
            )
        detail = response.json()["detail"]
        assert detail.get("request_id") == custom_id


# ===========================================================================
# 10. Global Error Handling
# ===========================================================================

class TestGlobalErrorHandling:
    """Verify unhandled exceptions are caught and return safe responses."""

    def test_unhandled_exception_returns_500_with_error_code(self):
        """Any unhandled exception should result in a 500 with error_code=INTERNAL_ERROR."""
        with patch(
            "app.api.v1.health_routes.verify_connection",
            side_effect=RuntimeError("something truly unexpected happened"),
        ):
            response = client.get("/api/v1/health")
        assert response.status_code == 500
        data = response.json()
        assert data.get("error_code") == "INTERNAL_ERROR"

    def test_unhandled_exception_message_is_safe(self):
        """Internal exception details must NOT appear in the 500 response body."""
        with patch(
            "app.api.v1.health_routes.verify_connection",
            side_effect=RuntimeError("super sensitive internal detail"),
        ):
            response = client.get("/api/v1/health")
        data = response.json()
        assert "super sensitive internal detail" not in data.get("message", "")

    def test_unhandled_exception_response_has_request_id(self):
        """A 500 from an unhandled exception should include request_id."""
        custom_id = "crash-trace-111"
        with patch(
            "app.api.v1.health_routes.verify_connection",
            side_effect=RuntimeError("boom"),
        ):
            response = client.get(
                "/api/v1/health", headers={"X-Request-ID": custom_id}
            )
        data = response.json()
        assert data.get("request_id") == custom_id
