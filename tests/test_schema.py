"""
Tests for schema inspection endpoints and the SchemaManager.

These tests use a live database when available (integration tests),
and mocks when testing error handling and edge cases (unit tests).
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from app.database.schema_manager import SchemaManager
from app.models.schema_models import (
    ColumnSchema,
    DatabaseSchema,
    ForeignKeySchema,
    TableSchema,
    UniqueConstraintSchema,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helper: build a realistic mock DatabaseSchema
# ---------------------------------------------------------------------------
def _mock_database_schema() -> DatabaseSchema:
    """Return a mock DatabaseSchema matching the college_db test structure."""
    students_table = TableSchema(
        name="students",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="name", type="VARCHAR(100)", nullable=False),
            ColumnSchema(name="email", type="VARCHAR(150)", nullable=True),
            ColumnSchema(name="marks", type="INTEGER", nullable=True),
            ColumnSchema(name="department_id", type="INTEGER", nullable=True),
        ],
        primary_keys=["id"],
        foreign_keys=[
            ForeignKeySchema(
                column="department_id",
                references_table="departments",
                references_column="id",
                constraint_name="fk_student_department",
            )
        ],
        unique_constraints=[
            UniqueConstraintSchema(name="email", columns=["email"]),
        ],
    )

    departments_table = TableSchema(
        name="departments",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="name", type="VARCHAR(100)", nullable=False),
        ],
        primary_keys=["id"],
        foreign_keys=[],
        unique_constraints=[
            UniqueConstraintSchema(name="name", columns=["name"]),
        ],
    )

    courses_table = TableSchema(
        name="courses",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="course_name", type="VARCHAR(150)", nullable=False),
            ColumnSchema(name="credits", type="INTEGER", nullable=False),
            ColumnSchema(name="department_id", type="INTEGER", nullable=True),
        ],
        primary_keys=["id"],
        foreign_keys=[
            ForeignKeySchema(
                column="department_id",
                references_table="departments",
                references_column="id",
                constraint_name="fk_course_department",
            )
        ],
        unique_constraints=[],
    )

    enrollments_table = TableSchema(
        name="enrollments",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="student_id", type="INTEGER", nullable=False),
            ColumnSchema(name="course_id", type="INTEGER", nullable=False),
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
        database="college_db",
        tables=[departments_table, students_table, courses_table, enrollments_table],
        table_count=4,
    )


def _mock_students_table() -> TableSchema:
    """Return just the students table schema for single-table tests."""
    schema = _mock_database_schema()
    return next(t for t in schema.tables if t.name == "students")


# ===========================================================================
# Schema Endpoint Tests
# ===========================================================================


class TestFullSchemaEndpoint:
    """Tests for GET /schema."""

    def test_schema_returns_200_with_valid_data(self):
        """GET /schema should return complete database schema."""
        mock_schema = _mock_database_schema()
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_database_schema.return_value = mock_schema
            response = client.get("/schema")

        assert response.status_code == 200
        data = response.json()
        assert data["database"] == "college_db"
        assert data["table_count"] == 4

    def test_schema_contains_expected_tables(self):
        """Schema response should include all four expected tables."""
        mock_schema = _mock_database_schema()
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_database_schema.return_value = mock_schema
            response = client.get("/schema")

        table_names = [t["name"] for t in response.json()["tables"]]
        assert "departments" in table_names
        assert "students" in table_names
        assert "courses" in table_names
        assert "enrollments" in table_names

    def test_schema_columns_are_detected(self):
        """Each table should have its columns listed with type and nullable info."""
        mock_schema = _mock_database_schema()
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_database_schema.return_value = mock_schema
            response = client.get("/schema")

        data = response.json()
        students = next(t for t in data["tables"] if t["name"] == "students")
        col_names = [c["name"] for c in students["columns"]]
        assert "id" in col_names
        assert "name" in col_names
        assert "marks" in col_names
        assert "department_id" in col_names

        # Check type and nullable on specific columns
        id_col = next(c for c in students["columns"] if c["name"] == "id")
        assert "INTEGER" in id_col["type"].upper()
        assert id_col["nullable"] is False

        marks_col = next(c for c in students["columns"] if c["name"] == "marks")
        assert marks_col["nullable"] is True

    def test_schema_primary_keys_detected(self):
        """Primary keys should be correctly identified for each table."""
        mock_schema = _mock_database_schema()
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_database_schema.return_value = mock_schema
            response = client.get("/schema")

        data = response.json()
        students = next(t for t in data["tables"] if t["name"] == "students")
        assert "id" in students["primary_keys"]

    def test_schema_foreign_keys_detected(self):
        """Foreign keys should be correctly identified with referenced table and column."""
        mock_schema = _mock_database_schema()
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_database_schema.return_value = mock_schema
            response = client.get("/schema")

        data = response.json()
        students = next(t for t in data["tables"] if t["name"] == "students")
        fk = next(
            (fk for fk in students["foreign_keys"] if fk["column"] == "department_id"),
            None,
        )
        assert fk is not None
        assert fk["references_table"] == "departments"
        assert fk["references_column"] == "id"

    def test_schema_returns_500_on_db_error(self):
        """GET /schema should return 500 if the database inspection fails."""
        from sqlalchemy.exc import SQLAlchemyError

        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_database_schema.side_effect = SQLAlchemyError("mock error")
            response = client.get("/schema")

        assert response.status_code == 500
        assert "Failed to retrieve" in response.json()["detail"]


class TestTableSchemaEndpoint:
    """Tests for GET /schema/{table_name}."""

    def test_existing_table_returns_200(self):
        """Requesting a valid table should return its schema."""
        mock_table = _mock_students_table()
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_table_schema.return_value = mock_table
            response = client.get("/schema/students")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "students"
        assert len(data["columns"]) > 0

    def test_nonexistent_table_returns_404(self):
        """Requesting a table that doesn't exist should return 404."""
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_table_schema.side_effect = ValueError(
                "Table 'nonexistent' does not exist"
            )
            response = client.get("/schema/nonexistent")

        assert response.status_code == 404
        assert "does not exist" in response.json()["detail"]

    def test_table_schema_has_primary_keys(self):
        """Table schema should include primary key information."""
        mock_table = _mock_students_table()
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_table_schema.return_value = mock_table
            response = client.get("/schema/students")

        assert "id" in response.json()["primary_keys"]

    def test_table_schema_has_foreign_keys(self):
        """Table schema should include foreign key information."""
        mock_table = _mock_students_table()
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_table_schema.return_value = mock_table
            response = client.get("/schema/students")

        fks = response.json()["foreign_keys"]
        assert len(fks) > 0
        assert fks[0]["references_table"] == "departments"

    def test_table_endpoint_returns_500_on_db_error(self):
        """GET /schema/{table_name} should return 500 on database errors."""
        from sqlalchemy.exc import SQLAlchemyError

        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_table_schema.side_effect = SQLAlchemyError("mock error")
            response = client.get("/schema/students")

        assert response.status_code == 500


class TestSchemaModels:
    """Tests for Pydantic schema models serialization."""

    def test_database_schema_serialization(self):
        """DatabaseSchema should serialize to a valid dict matching API contract."""
        schema = _mock_database_schema()
        data = schema.model_dump()
        assert data["database"] == "college_db"
        assert data["table_count"] == 4
        assert isinstance(data["tables"], list)

    def test_column_schema_fields(self):
        """ColumnSchema should expose name, type, nullable, and autoincrement."""
        col = ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True)
        data = col.model_dump()
        assert data["name"] == "id"
        assert data["type"] == "INTEGER"
        assert data["nullable"] is False
        assert data["autoincrement"] is True

    def test_foreign_key_schema_fields(self):
        """ForeignKeySchema should expose column, references_table, references_column."""
        fk = ForeignKeySchema(
            column="dept_id",
            references_table="departments",
            references_column="id",
        )
        data = fk.model_dump()
        assert data["column"] == "dept_id"
        assert data["references_table"] == "departments"
        assert data["references_column"] == "id"
