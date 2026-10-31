"""
Tests for database connection and the /health endpoint.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app
from app.database.connection import verify_connection


client = TestClient(app)


class TestHealthEndpoint:
    """Tests for the GET /health endpoint."""

    def test_health_returns_200_when_db_connected(self):
        """Health check should return 200 with connected status when DB is reachable."""
        with patch("app.api.schema_routes.verify_connection", return_value=True):
            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["database"] == "connected"

    def test_health_returns_503_when_db_disconnected(self):
        """Health check should return 503 when the database is unreachable."""
        with patch("app.api.schema_routes.verify_connection", return_value=False):
            response = client.get("/health")
            assert response.status_code == 503
            data = response.json()["detail"]
            assert data["status"] == "error"
            assert data["database"] == "disconnected"


class TestVerifyConnection:
    """Tests for the verify_connection utility function."""

    def test_verify_connection_returns_bool(self):
        """verify_connection should return a boolean."""
        result = verify_connection()
        assert isinstance(result, bool)

    def test_verify_connection_with_bad_engine(self):
        """Connection verification should return False when the engine can't connect."""
        with patch("app.database.connection.get_engine") as mock_engine:
            from sqlalchemy.exc import OperationalError
            mock_conn = mock_engine.return_value.connect.return_value.__enter__
            mock_conn.side_effect = OperationalError("mock", "mock", Exception("mock"))
            result = verify_connection()
            assert result is False


class TestAppStartup:
    """Tests verifying the application initializes correctly."""

    def test_app_is_created(self):
        """FastAPI app instance should exist and have the correct title."""
        assert app is not None
        assert app.title == "NL2SQL - Database Schema Manager"

    def test_app_has_routes(self):
        """The app should have /health, /schema, and /schema/{table_name} routes."""
        openapi = app.openapi()
        paths = list(openapi["paths"].keys())
        assert "/health" in paths
        assert "/schema" in paths
        assert "/schema/{table_name}" in paths
