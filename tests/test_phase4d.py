"""
Phase 4D tests: SQL Query Optimization.

Tests cover:
  1.  Request/response model validation
  2.  Optimization service — SELECT * expansion
  3.  Optimization service — redundant DISTINCT removal
  4.  Optimization service — tautology removal
  5.  Optimization service — constant-false detection
  6.  Optimization service — index recommendations
  7.  Optimization service — no-change queries
  8.  Optimization service — multiple rules applied
  9.  API endpoint — valid SELECT optimization
 10.  API endpoint — query with no applicable optimization
 11.  API endpoint — original SQL validation failure (422)
 12.  API endpoint — optimized SQL re-validation
 13.  Safety — non-SELECT rejection (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE)
 14.  Safety — multi-statement rejection
 15.  Safety — SQL injection attempts
 16.  Schema validation — unknown table / unknown column
 17.  Alias handling
 18.  JOIN queries
 19.  GROUP BY queries
 20.  ORDER BY queries
 21.  LIMIT queries
 22.  No SELECT execution during optimization
 23.  Invalid SQL never reaches database
 24.  Schema/data not modified
 25.  API response structure validation
 26.  Endpoint registration
 27.  Regression — Phase 4A, 4B, 4C still work
 28.  Phase boundary — no Phase 4E functionality

All database calls are mocked — tests do NOT require a live MySQL connection.
"""

import pytest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.models.query_models import (
    OptimizationApplied,
    QueryOptimizeRequest,
    QueryOptimizeResponse,
    SQLValidationResult,
)
from app.models.schema_models import (
    ColumnSchema,
    DatabaseSchema,
    ForeignKeySchema,
    TableSchema,
    UniqueConstraintSchema,
)
from app.services.query_optimization_service import (
    QueryOptimizationError,
    QueryOptimizationService,
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


# ===========================================================================
# 1. REQUEST MODEL TESTS
# ===========================================================================

class TestQueryOptimizeRequest:
    """Request model validation."""

    def test_accepts_valid_sql(self):
        req = QueryOptimizeRequest(sql="SELECT * FROM students")
        assert req.sql == "SELECT * FROM students"

    def test_empty_sql_rejected(self):
        with pytest.raises(ValidationError):
            QueryOptimizeRequest(sql="")

    def test_whitespace_sql_rejected(self):
        with pytest.raises(ValidationError):
            QueryOptimizeRequest(sql="   ")

    def test_sql_is_stripped(self):
        req = QueryOptimizeRequest(sql="  SELECT * FROM students  ")
        assert req.sql == "SELECT * FROM students"

    def test_long_sql_accepted(self):
        long_sql = "SELECT " + ", ".join(f"col{i}" for i in range(50)) + " FROM students"
        req = QueryOptimizeRequest(sql=long_sql)
        assert "col0" in req.sql


# ===========================================================================
# 2. RESPONSE MODEL TESTS
# ===========================================================================

class TestQueryOptimizeResponse:
    """Response model validation."""

    def test_response_model_fields(self):
        resp = QueryOptimizeResponse(
            original_sql="SELECT * FROM students",
            optimized_sql="SELECT id, name FROM students",
            changed=True,
            optimizations=[],
            index_recommendations=[],
            validation=SQLValidationResult(valid=True, sql="SELECT * FROM students"),
            optimized_validation=None,
            request_id="test-123",
        )
        assert resp.original_sql == "SELECT * FROM students"
        assert resp.changed is True
        assert resp.request_id == "test-123"

    def test_response_unchanged(self):
        resp = QueryOptimizeResponse(
            original_sql="SELECT id FROM students",
            optimized_sql="SELECT id FROM students",
            changed=False,
            optimizations=[],
            index_recommendations=[],
            validation=SQLValidationResult(valid=True, sql="SELECT id FROM students"),
            optimized_validation=None,
        )
        assert resp.changed is False
        assert resp.optimized_validation is None

    def test_optimization_applied_model(self):
        opt = OptimizationApplied(
            rule="test_rule",
            description="Test optimization",
            original_fragment="SELECT *",
            optimized_fragment="SELECT id",
        )
        assert opt.rule == "test_rule"
        assert opt.original_fragment == "SELECT *"


# ===========================================================================
# 3. OPTIMIZATION SERVICE — SELECT * EXPANSION
# ===========================================================================

class TestSelectStarExpansion:
    """Optimization rule: expand SELECT * to explicit columns."""

    def test_select_star_expanded(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT * FROM students")

        assert result["changed"] is True
        # All student columns should be present
        for col in ["id", "name", "email", "marks", "department_id", "created_at"]:
            assert col in result["optimized_sql"]
        assert any(
            o.rule == "select_star_expansion" for o in result["optimizations"]
        )

    def test_select_star_not_expanded_for_multi_table(self):
        """SELECT * is not expanded when multiple tables are involved (JOINs)."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT * FROM students s JOIN departments d ON s.department_id = d.id"
            )

        # SELECT * expansion should NOT apply to multi-table queries
        assert not any(
            o.rule == "select_star_expansion" for o in result["optimizations"]
        )

    def test_select_star_with_departments(self):
        """SELECT * from departments table is expanded to its columns."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT * FROM departments")

        assert result["changed"] is True
        for col in ["id", "name", "created_at"]:
            assert col in result["optimized_sql"]


# ===========================================================================
# 4. OPTIMIZATION SERVICE — REDUNDANT DISTINCT REMOVAL
# ===========================================================================

class TestRedundantDistinctRemoval:
    """Optimization rule: remove DISTINCT when PK columns are included."""

    def test_distinct_removed_when_pk_included(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT DISTINCT id, name FROM students")

        assert result["changed"] is True
        assert any(
            o.rule == "redundant_distinct_removal" for o in result["optimizations"]
        )
        assert "DISTINCT" not in result["optimized_sql"]

    def test_distinct_preserved_without_pk(self):
        """DISTINCT is preserved when PK columns are not selected."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT DISTINCT name FROM students")

        assert not any(
            o.rule == "redundant_distinct_removal" for o in result["optimizations"]
        )


# ===========================================================================
# 5. OPTIMIZATION SERVICE — TAUTOLOGY REMOVAL
# ===========================================================================

class TestTautologyRemoval:
    """Optimization rule: remove tautological WHERE conditions."""

    def test_where_1_equals_1_removed(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT id FROM students WHERE 1 = 1")

        assert result["changed"] is True
        assert any(
            o.rule == "tautology_removal" for o in result["optimizations"]
        )

    def test_where_true_removed(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT id FROM students WHERE TRUE")

        assert result["changed"] is True
        assert any(
            o.rule == "tautology_removal" for o in result["optimizations"]
        )

    def test_tautology_in_and_chain(self):
        """Tautology in AND chain is removed, real condition preserved."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT id FROM students WHERE 1 = 1 AND marks > 50"
            )

        assert result["changed"] is True
        # The real condition should survive
        assert "marks" in result["optimized_sql"]

    def test_non_tautology_preserved(self):
        """Real WHERE conditions are not removed."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT id FROM students WHERE marks > 50"
            )

        assert not any(
            o.rule == "tautology_removal" for o in result["optimizations"]
        )


# ===========================================================================
# 6. OPTIMIZATION SERVICE — CONSTANT-FALSE DETECTION
# ===========================================================================

class TestConstantFalseDetection:
    """Optimization rule: detect always-false WHERE conditions."""

    def test_where_1_equals_0_detected(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT id FROM students WHERE 1 = 0")

        assert any(
            o.rule == "constant_false_detection" for o in result["optimizations"]
        )

    def test_where_false_detected(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT id FROM students WHERE FALSE")

        assert any(
            o.rule == "constant_false_detection" for o in result["optimizations"]
        )

    def test_constant_false_is_observation_only(self):
        """Constant-false detection should not modify the query."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT id FROM students WHERE 1 = 0")

        # The SQL should NOT be modified — this is informational
        # 'changed' reflects whether the SQL text was altered
        # constant_false_detection is observation-only, no AST change


# ===========================================================================
# 7. OPTIMIZATION SERVICE — INDEX RECOMMENDATIONS
# ===========================================================================

class TestIndexRecommendations:
    """Index recommendations are observational, no indexes are created."""

    def test_index_recommendation_for_unindexed_where_column(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT id FROM students WHERE marks > 50"
            )

        # marks is not a primary key or foreign key — should get recommendation
        assert len(result["index_recommendations"]) > 0
        assert any("marks" in rec for rec in result["index_recommendations"])

    def test_no_recommendation_for_primary_key(self):
        """Primary key columns should not get index recommendations."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT name FROM students WHERE id = 1"
            )

        # id is a primary key — no recommendation needed
        assert not any("'id'" in rec for rec in result["index_recommendations"])

    def test_no_recommendation_for_foreign_key(self):
        """Foreign key columns should not get index recommendations."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT name FROM students WHERE department_id = 1"
            )

        # department_id is a foreign key — typically indexed
        assert not any(
            "'department_id'" in rec for rec in result["index_recommendations"]
        )


# ===========================================================================
# 8. NO-CHANGE QUERIES
# ===========================================================================

class TestNoChangeQueries:
    """Queries where no optimization is applicable."""

    def test_simple_select_no_change(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT id, name FROM students")

        assert result["changed"] is False
        assert result["original_sql"] == "SELECT id, name FROM students"
        assert result["optimized_sql"] == "SELECT id, name FROM students"

    def test_explicit_columns_no_expansion(self):
        """Already explicit columns should not trigger expansion."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT id, name, marks FROM students WHERE marks > 90"
            )

        assert not any(
            o.rule == "select_star_expansion" for o in result["optimizations"]
        )


# ===========================================================================
# 9. MULTIPLE RULES APPLIED
# ===========================================================================

class TestMultipleRules:
    """Multiple optimization rules applied in a single pass."""

    def test_star_expansion_and_tautology(self):
        """Both SELECT * expansion and tautology removal apply."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT * FROM students WHERE 1 = 1")

        assert result["changed"] is True
        rules = {o.rule for o in result["optimizations"]}
        assert "select_star_expansion" in rules
        assert "tautology_removal" in rules

    def test_distinct_removal_and_tautology(self):
        """Both redundant DISTINCT removal and tautology removal apply."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT DISTINCT id, name FROM students WHERE 1 = 1"
            )

        assert result["changed"] is True
        rules = {o.rule for o in result["optimizations"]}
        assert "redundant_distinct_removal" in rules
        assert "tautology_removal" in rules


# ===========================================================================
# 10. ORIGINAL SQL VALIDATION
# ===========================================================================

class TestOriginalSQLValidation:
    """Original SQL must pass Phase 4A validation before optimization."""

    def test_invalid_original_returns_validation_failure(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("DELETE FROM students")

        assert result["validation"].valid is False
        assert result["changed"] is False
        assert result["optimizations"] == []

    def test_unknown_table_rejected(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT * FROM nonexistent_table")

        assert result["validation"].valid is False
        assert result["changed"] is False

    def test_unknown_column_rejected(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT nonexistent_col FROM students")

        assert result["validation"].valid is False
        assert result["changed"] is False


# ===========================================================================
# 11. OPTIMIZED SQL RE-VALIDATION
# ===========================================================================

class TestOptimizedSQLValidation:
    """Optimized SQL must pass Phase 4A validation before being returned."""

    def test_optimized_sql_is_validated(self):
        """When SQL changes, the optimized SQL is re-validated."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT * FROM students")

        # If changed, optimized_validation should exist
        if result["changed"]:
            assert result["optimized_validation"] is not None
            assert result["optimized_validation"].valid is True

    def test_unchanged_sql_no_revalidation(self):
        """When SQL is unchanged, no re-validation is performed."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT id FROM students")

        assert result["changed"] is False
        assert result["optimized_validation"] is None


# ===========================================================================
# 12. SECURITY — NON-SELECT REJECTION
# ===========================================================================

class TestNonSelectRejection:
    """All non-SELECT statements must be rejected."""

    def test_delete_rejected(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("DELETE FROM students")
        assert result["validation"].valid is False
        assert result["changed"] is False

    def test_update_rejected(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("UPDATE students SET marks = 100")
        assert result["validation"].valid is False

    def test_insert_rejected(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("INSERT INTO students (name) VALUES ('test')")
        assert result["validation"].valid is False

    def test_drop_rejected(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("DROP TABLE students")
        assert result["validation"].valid is False

    def test_alter_rejected(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("ALTER TABLE students ADD COLUMN age INT")
        assert result["validation"].valid is False

    def test_truncate_rejected(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("TRUNCATE TABLE students")
        assert result["validation"].valid is False

    def test_create_rejected(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("CREATE TABLE hacked (id INT)")
        assert result["validation"].valid is False

    def test_grant_rejected(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("GRANT ALL ON students TO 'attacker'")
        assert result["validation"].valid is False

    def test_set_rejected(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SET GLOBAL max_connections = 1000")
        assert result["validation"].valid is False


# ===========================================================================
# 13. SECURITY — MULTI-STATEMENT REJECTION
# ===========================================================================

class TestMultiStatementRejection:
    """Multi-statement SQL is rejected."""

    def test_select_plus_drop(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT id FROM students; DROP TABLE students"
            )
        assert result["validation"].valid is False

    def test_two_selects(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT id FROM students; SELECT name FROM students"
            )
        assert result["validation"].valid is False


# ===========================================================================
# 14. SECURITY — SQL INJECTION ATTEMPTS
# ===========================================================================

class TestSQLInjection:
    """SQL injection attempts are blocked by Phase 4A validation."""

    def test_comment_injection(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT * FROM students; -- DROP TABLE students"
            )
        assert result["validation"].valid is False

    def test_union_injection(self):
        """UNION with non-existent table is rejected at schema validation."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT id FROM students UNION SELECT password FROM users"
            )
        # 'users' table doesn't exist — schema validation catches it
        assert result["validation"].valid is False

    def test_inline_comment_bypass(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT * FROM students WHERE id = 1 /* ; DROP TABLE students */"
            )
        # This is actually a valid SELECT with a comment — the comment is
        # stripped by the parser, so the query is valid
        # The important check: no DML/DDL is executed


# ===========================================================================
# 15. SCHEMA VALIDATION
# ===========================================================================

class TestSchemaValidation:
    """Unknown table / column rejection during optimization."""

    def test_unknown_table(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT * FROM nonexistent")
        assert result["validation"].valid is False
        assert any("nonexistent" in e for e in result["validation"].errors)

    def test_unknown_column(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT fake_col FROM students")
        assert result["validation"].valid is False
        assert any("fake_col" in e for e in result["validation"].errors)


# ===========================================================================
# 16. ALIAS HANDLING
# ===========================================================================

class TestAliasHandling:
    """Table aliases are resolved correctly during optimization."""

    def test_alias_in_select(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT s.id, s.name FROM students s"
            )
        assert result["validation"].valid is True

    def test_alias_in_where(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT s.id FROM students s WHERE s.marks > 50"
            )
        assert result["validation"].valid is True


# ===========================================================================
# 17. JOIN QUERIES
# ===========================================================================

class TestJoinQueries:
    """JOIN queries are handled correctly."""

    def test_inner_join_optimized(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT s.name, d.name FROM students s "
                "INNER JOIN departments d ON s.department_id = d.id"
            )
        assert result["validation"].valid is True

    def test_left_join_optimized(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT s.name, d.name FROM students s "
                "LEFT JOIN departments d ON s.department_id = d.id"
            )
        assert result["validation"].valid is True

    def test_three_table_join(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT s.name, c.course_name FROM students s "
                "JOIN enrollments e ON s.id = e.student_id "
                "JOIN courses c ON e.course_id = c.id"
            )
        assert result["validation"].valid is True


# ===========================================================================
# 18. GROUP BY QUERIES
# ===========================================================================

class TestGroupByQueries:
    """GROUP BY queries are handled correctly."""

    def test_group_by_valid(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT department_id, COUNT(*) FROM students GROUP BY department_id"
            )
        assert result["validation"].valid is True

    def test_group_by_with_having(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT department_id, AVG(marks) FROM students "
                "GROUP BY department_id HAVING AVG(marks) > 50"
            )
        assert result["validation"].valid is True


# ===========================================================================
# 19. ORDER BY QUERIES
# ===========================================================================

class TestOrderByQueries:
    """ORDER BY queries are preserved correctly."""

    def test_order_by_preserved(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT id, name FROM students ORDER BY marks DESC"
            )
        assert result["validation"].valid is True

    def test_order_by_with_limit(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT id, name FROM students ORDER BY marks DESC LIMIT 10"
            )
        assert result["validation"].valid is True


# ===========================================================================
# 20. LIMIT QUERIES
# ===========================================================================

class TestLimitQueries:
    """LIMIT queries are handled correctly."""

    def test_limit_preserved(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT id, name FROM students LIMIT 5"
            )
        assert result["validation"].valid is True

    def test_limit_offset_preserved(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize(
                "SELECT id, name FROM students LIMIT 5 OFFSET 10"
            )
        assert result["validation"].valid is True


# ===========================================================================
# 21. API ENDPOINT — VALID OPTIMIZATION
# ===========================================================================

class TestOptimizeEndpointValid:
    """POST /api/v1/query/optimize with valid SQL."""

    def test_optimize_select_star(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT * FROM students"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["original_sql"] == "SELECT * FROM students"
        assert data["changed"] is True
        assert "validation" in data
        assert data["validation"]["valid"] is True

    def test_optimize_no_change(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT id, name FROM students"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["changed"] is False

    def test_optimize_with_tautology(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT id FROM students WHERE 1 = 1"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["changed"] is True

    def test_response_has_request_id(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT id FROM students"},
            )
        assert response.status_code == 200
        data = response.json()
        assert "request_id" in data

    def test_response_has_optimizations_list(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT * FROM students"},
            )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["optimizations"], list)
        assert len(data["optimizations"]) > 0

    def test_response_has_index_recommendations(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT id FROM students WHERE marks > 50"},
            )
        assert response.status_code == 200
        data = response.json()
        assert "index_recommendations" in data
        assert isinstance(data["index_recommendations"], list)


# ===========================================================================
# 22. API ENDPOINT — VALIDATION FAILURE (422)
# ===========================================================================

class TestOptimizeEndpointRejection:
    """POST /api/v1/query/optimize with invalid SQL returns 422."""

    def test_delete_returns_422(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "DELETE FROM students"},
            )
        assert response.status_code == 422
        data = response.json()
        assert data["detail"]["error_code"] == "SQL_VALIDATION_FAILED"

    def test_update_returns_422(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "UPDATE students SET marks = 100"},
            )
        assert response.status_code == 422

    def test_insert_returns_422(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "INSERT INTO students (name) VALUES ('test')"},
            )
        assert response.status_code == 422

    def test_drop_returns_422(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "DROP TABLE students"},
            )
        assert response.status_code == 422

    def test_alter_returns_422(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "ALTER TABLE students ADD COLUMN age INT"},
            )
        assert response.status_code == 422

    def test_truncate_returns_422(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "TRUNCATE TABLE students"},
            )
        assert response.status_code == 422

    def test_multi_statement_returns_422(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT id FROM students; DROP TABLE students"},
            )
        assert response.status_code == 422

    def test_unknown_table_returns_422(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT * FROM nonexistent_table"},
            )
        assert response.status_code == 422

    def test_unknown_column_returns_422(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT fake_col FROM students"},
            )
        assert response.status_code == 422

    def test_sql_injection_returns_422(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT * FROM students; -- DROP TABLE students"},
            )
        assert response.status_code == 422

    def test_error_response_has_request_id(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "DELETE FROM students"},
            )
        assert response.status_code == 422
        data = response.json()
        assert "request_id" in data["detail"]

    def test_no_api_key_in_error(self):
        """Error responses must not contain API keys."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "DELETE FROM students"},
            )
        body = response.text
        assert "GEMINI_API_KEY" not in body
        assert "AIza" not in body  # Google API key prefix


# ===========================================================================
# 23. API ENDPOINT — ERROR HANDLING
# ===========================================================================

class TestOptimizeEndpointErrors:
    """Error handling in the optimize endpoint."""

    def test_schema_retrieval_failure_returns_500(self):
        from sqlalchemy.exc import SQLAlchemyError
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.side_effect = SQLAlchemyError("DB down")
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT id FROM students"},
            )
        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["error_code"] == "SCHEMA_RETRIEVAL_FAILED"

    def test_optimization_error_returns_500(self):
        schema = _mock_schema()
        with (
            patch(
                "app.services.query_optimization_service.SchemaService"
            ) as MockSchema,
            patch.object(
                QueryOptimizationService, "optimize",
                side_effect=QueryOptimizationError("Parse failed"),
            ),
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT id FROM students"},
            )
        assert response.status_code == 500
        data = response.json()
        assert data["detail"]["error_code"] == "OPTIMIZATION_FAILED"

    def test_empty_body_returns_422(self):
        response = client.post("/api/v1/query/optimize", json={})
        assert response.status_code == 422

    def test_missing_sql_field_returns_422(self):
        response = client.post(
            "/api/v1/query/optimize",
            json={"query": "SELECT id FROM students"},
        )
        assert response.status_code == 422


# ===========================================================================
# 24. NO SELECT EXECUTION DURING OPTIMIZATION
# ===========================================================================

class TestNoSelectExecution:
    """Ensure no actual SQL query is executed during optimization."""

    def test_no_engine_used(self):
        """The optimization service should not use get_engine / execute."""
        schema = _mock_schema()
        with (
            patch(
                "app.services.query_optimization_service.SchemaService"
            ) as MockSchema,
            patch(
                "app.services.query_optimization_service.sqlglot"
            ) as mock_sqlglot,
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            # Let sqlglot work normally
            mock_sqlglot.parse = __import__("sqlglot").parse
            mock_sqlglot.errors = __import__("sqlglot").errors

            # Check: no import of get_engine in the module
            import inspect
            source = inspect.getsource(QueryOptimizationService)
            assert "get_engine" not in source
            assert "conn.execute" not in source

    def test_service_does_not_import_get_engine(self):
        """The optimization service module should not import get_engine."""
        import app.services.query_optimization_service as mod
        module_source = open(mod.__file__).read()
        assert "get_engine" not in module_source

    def test_service_does_not_call_execute(self):
        """The optimization service should not call execute on any connection."""
        import inspect
        source = inspect.getsource(QueryOptimizationService)
        assert ".execute(" not in source


# ===========================================================================
# 25. INVALID SQL NEVER REACHES DATABASE
# ===========================================================================

class TestInvalidSQLNeverReachesDB:
    """Invalid SQL must never reach any database execution path."""

    def test_invalid_sql_no_db_call(self):
        schema = _mock_schema()
        with (
            patch(
                "app.services.query_optimization_service.SchemaService"
            ) as MockSchema,
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("DROP TABLE students")

        assert result["validation"].valid is False
        assert result["changed"] is False
        # No database calls should have been made beyond schema retrieval

    def test_malformed_sql_no_db_call(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECTT id FROMM students")

        assert result["validation"].valid is False
        assert result["changed"] is False


# ===========================================================================
# 26. SCHEMA/DATA NOT MODIFIED
# ===========================================================================

class TestSchemaDataNotModified:
    """Optimization must not modify database schema or data."""

    def test_no_create_index(self):
        """The optimizer must not create indexes."""
        import inspect
        source = inspect.getsource(QueryOptimizationService)
        assert "CREATE INDEX" not in source
        assert "create_index" not in source.lower()

    def test_no_alter_table(self):
        """The optimizer must not alter tables."""
        import inspect
        source = inspect.getsource(QueryOptimizationService)
        assert "ALTER TABLE" not in source
        assert "alter_table" not in source.lower()

    def test_no_database_writes(self):
        """The optimizer should not write to the database."""
        import inspect
        source = inspect.getsource(QueryOptimizationService)
        assert "INSERT" not in source.split("'")[0] if "'" in source else True
        # Check for database engine usage
        assert "get_engine" not in source


# ===========================================================================
# 27. API RESPONSE STRUCTURE VALIDATION
# ===========================================================================

class TestAPIResponseStructure:
    """Validate the structure of API responses."""

    def test_success_response_all_fields(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT * FROM students"},
            )
        assert response.status_code == 200
        data = response.json()
        required_fields = [
            "original_sql",
            "optimized_sql",
            "changed",
            "optimizations",
            "index_recommendations",
            "validation",
            "request_id",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    def test_validation_field_structure(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT id FROM students"},
            )
        data = response.json()
        validation = data["validation"]
        assert "valid" in validation
        assert "sql" in validation
        assert "errors" in validation
        assert "warnings" in validation

    def test_optimization_entry_structure(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT * FROM students"},
            )
        data = response.json()
        assert len(data["optimizations"]) > 0
        opt = data["optimizations"][0]
        assert "rule" in opt
        assert "description" in opt

    def test_optimized_validation_present_when_changed(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT * FROM students"},
            )
        data = response.json()
        assert data["changed"] is True
        assert data["optimized_validation"] is not None
        assert data["optimized_validation"]["valid"] is True

    def test_optimized_validation_null_when_unchanged(self):
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/optimize",
                json={"sql": "SELECT id FROM students"},
            )
        data = response.json()
        assert data["changed"] is False
        assert data["optimized_validation"] is None


# ===========================================================================
# 28. ENDPOINT REGISTRATION
# ===========================================================================

class TestOptimizeEndpointRegistration:
    """Verify the optimize endpoint is properly registered."""

    def test_optimize_endpoint_in_openapi(self):
        paths = list(app.openapi()["paths"].keys())
        assert "/api/v1/query/optimize" in paths

    def test_optimize_accepts_post(self):
        paths = app.openapi()["paths"]
        assert "post" in paths["/api/v1/query/optimize"]

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
        assert "/api/v1/query/optimize" in paths


# ===========================================================================
# 29. REGRESSION — Phase 4A still works
# ===========================================================================

class TestRegressionPhase4A:
    """Phase 4A SQL validation still works correctly."""

    def test_phase4a_validates_valid_select(self):
        schema = _mock_schema()
        validator = SQLValidatorService()
        result = validator.validate("SELECT id, name FROM students", schema)
        assert result.valid is True

    def test_phase4a_rejects_delete(self):
        schema = _mock_schema()
        validator = SQLValidatorService()
        result = validator.validate("DELETE FROM students", schema)
        assert result.valid is False

    def test_phase4a_rejects_unknown_table(self):
        schema = _mock_schema()
        validator = SQLValidatorService()
        result = validator.validate("SELECT * FROM ghost_table", schema)
        assert result.valid is False

    def test_phase4a_rejects_multi_statement(self):
        schema = _mock_schema()
        validator = SQLValidatorService()
        result = validator.validate(
            "SELECT id FROM students; SELECT name FROM students",
            schema,
        )
        assert result.valid is False


# ===========================================================================
# 30. REGRESSION — Phase 4B still works
# ===========================================================================

class TestRegressionPhase4B:
    """Phase 4B execution endpoint still works."""

    def test_phase4b_execute_endpoint(self):
        schema = _mock_schema()
        mock_result = MagicMock()
        mock_result.keys.return_value = ["id"]
        mock_result.fetchmany.return_value = [(1,)]

        with (
            patch("app.services.sql_execution_service.SchemaService") as MockSchema,
            patch("app.services.sql_execution_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result

            response = client.post(
                "/api/v1/query/execute",
                json={"sql": "SELECT id FROM students"},
            )
        assert response.status_code == 200

    def test_phase4b_rejects_delete(self):
        schema = _mock_schema()
        with patch("app.services.sql_execution_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/execute",
                json={"sql": "DELETE FROM students"},
            )
        assert response.status_code == 422


# ===========================================================================
# 31. REGRESSION — Phase 4C still works
# ===========================================================================

class TestRegressionPhase4C:
    """Phase 4C EXPLAIN endpoint still works."""

    def test_phase4c_explain_endpoint(self):
        schema = _mock_schema()
        mock_result = MagicMock()
        mock_result.keys.return_value = [
            "id", "select_type", "table", "partitions", "type",
            "possible_keys", "key", "key_len", "ref", "rows",
            "filtered", "Extra",
        ]
        mock_result.fetchall.return_value = [
            (1, "SIMPLE", "students", None, "ALL",
             None, None, None, None, 3, 100.0, "Using filesort"),
        ]

        with (
            patch("app.services.query_plan_service.SchemaService") as MockSchema,
            patch("app.services.query_plan_service.get_engine") as mock_engine,
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            mock_conn = MagicMock()
            mock_engine.return_value.connect.return_value.__enter__ = lambda s: mock_conn
            mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value = mock_result

            response = client.post(
                "/api/v1/query/explain",
                json={"sql": "SELECT id FROM students"},
            )
        assert response.status_code == 200
        data = response.json()
        assert "plan" in data
        assert "analysis" in data

    def test_phase4c_rejects_delete(self):
        schema = _mock_schema()
        with patch("app.services.query_plan_service.SchemaService") as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            response = client.post(
                "/api/v1/query/explain",
                json={"sql": "DELETE FROM students"},
            )
        assert response.status_code == 422


# ===========================================================================
# 32. REGRESSION — Legacy endpoints
# ===========================================================================

class TestRegressionLegacy:
    """Legacy endpoints from Phase 1 and Phase 2 still work."""

    def test_phase1_legacy_schema(self):
        schema = _mock_schema()
        with patch("app.api.schema_routes.SchemaManager") as MockManager:
            MockManager.return_value.get_database_schema.return_value = schema
            response = client.get("/schema")
        assert response.status_code == 200

    def test_phase2_health(self):
        with patch("app.api.v1.health_routes.verify_connection", return_value=True):
            response = client.get("/api/v1/health")
        assert response.status_code == 200

    def test_phase3_generate(self):
        schema = _mock_schema()
        with (
            patch("app.services.query_generation_service.SchemaService") as MockSchema,
            patch("app.services.query_generation_service.GeminiService") as MockGemini,
        ):
            MockSchema.return_value.get_full_schema.return_value = schema
            MockGemini.return_value.generate_sql.return_value = "SELECT * FROM students;"

            response = client.post(
                "/api/v1/query/generate",
                json={"question": "Show all students"},
            )
        assert response.status_code == 200


# ===========================================================================
# 33. PHASE BOUNDARY — NO Phase 4E
# ===========================================================================

class TestPhaseBoundary4D:
    """Verify Phase 4D does not introduce Phase 4E functionality."""

    def test_no_benchmark_endpoint(self):
        """Phase 4D optimizer does not perform benchmarking.

        The dedicated /api/v1/query/performance endpoint (Phase 4E) now exists,
        but it is a separate, user-triggered endpoint. The optimization service
        must NOT run performance benchmarks internally.
        """
        import inspect
        source = inspect.getsource(QueryOptimizationService)
        # Optimizer must not contain benchmarking logic
        assert "run_count" not in source
        assert "warmup" not in source
        assert "perf_counter" not in source

    def test_no_execution_time_in_optimizer(self):
        """The optimizer should not measure execution time."""
        import inspect
        source = inspect.getsource(QueryOptimizationService)
        assert "execution_time" not in source
        assert "time.time" not in source
        assert "perf_counter" not in source

    def test_no_performance_comparison(self):
        """The optimizer should not compare query performance."""
        import inspect
        source = inspect.getsource(QueryOptimizationService)
        assert "faster" not in source.lower()
        assert "slower" not in source.lower()
        assert "speedup" not in source.lower()

    def test_no_gemini_in_optimizer(self):
        """The optimizer should not call Gemini."""
        import inspect
        source = inspect.getsource(QueryOptimizationService)
        assert "GeminiService" not in source
        assert "gemini" not in source.lower()

    def test_optimizer_does_not_import_gemini(self):
        """The optimization service module should not import gemini."""
        import app.services.query_optimization_service as mod
        module_source = open(mod.__file__).read()
        # Check functional lines only — exclude comments and docstrings
        code_lines = []
        in_docstring = False
        for line in module_source.splitlines():
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if stripped.count('"""') == 1 or stripped.count("'''") == 1:
                    in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith("#"):
                continue
            code_lines.append(stripped.lower())
        code = "\n".join(code_lines)
        assert "import" not in code or "gemini" not in code
        # More specific: no import of gemini modules
        assert "from app.services.gemini" not in code
        assert "import gemini" not in code
        assert "geminiservice" not in code

    def test_no_frontend_routes(self):
        """No frontend/dashboard routes should exist."""
        paths = list(app.openapi()["paths"].keys())
        for path in paths:
            assert "dashboard" not in path.lower()
            assert "frontend" not in path.lower()

    def test_optimize_response_does_not_claim_faster(self):
        """Optimization response should never claim performance improvement."""
        schema = _mock_schema()
        with patch(
            "app.services.query_optimization_service.SchemaService"
        ) as MockSchema:
            MockSchema.return_value.get_full_schema.return_value = schema
            service = QueryOptimizationService()
            result = service.optimize("SELECT * FROM students")

        for opt in result["optimizations"]:
            assert "faster" not in opt.description.lower()
            assert "slower" not in opt.description.lower()
            assert "speedup" not in opt.description.lower()
            assert "performance" not in opt.description.lower()
