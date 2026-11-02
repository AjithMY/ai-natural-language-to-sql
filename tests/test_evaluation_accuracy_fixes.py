"""
Comprehensive regression tests for Evaluation Accuracy Improvement:
  - Task 1: Schema Availability Service false-positive fixes and missing-schema preservation.
  - Task 2: SQL Validator Service ORDER BY alias validation, scoping, backticks, and security.
"""

import pytest

from app.models.schema_models import (
    ColumnSchema,
    DatabaseSchema,
    ForeignKeySchema,
    TableSchema,
)
from app.services.schema_availability_service import SchemaAvailabilityService
from app.services.sql_validator import SQLValidatorService


# ---------------------------------------------------------------------------
# Test Fixtures: Standard Schema
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_college_schema() -> DatabaseSchema:
    """Mock college schema matching college_large_demo structure."""
    departments = TableSchema(
        name="departments",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="name", type="VARCHAR(100)", nullable=False),
            ColumnSchema(name="hod_name", type="VARCHAR(100)", nullable=True),
            ColumnSchema(name="building", type="VARCHAR(100)", nullable=True),
        ],
        primary_keys=["id"],
        source="live",
    )
    students = TableSchema(
        name="students",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="register_no", type="VARCHAR(30)", nullable=False),
            ColumnSchema(name="name", type="VARCHAR(100)", nullable=False),
            ColumnSchema(name="email", type="VARCHAR(150)", nullable=False),
            ColumnSchema(name="gender", type="ENUM", nullable=False),
            ColumnSchema(name="department_id", type="INTEGER", nullable=True),
            ColumnSchema(name="cgpa", type="DECIMAL(4,2)", nullable=True),
            ColumnSchema(name="marks", type="INTEGER", nullable=True),
            ColumnSchema(name="status", type="ENUM", nullable=False),
        ],
        primary_keys=["id"],
        foreign_keys=[
            ForeignKeySchema(
                column="department_id",
                references_table="departments",
                references_column="id",
            )
        ],
        source="live",
    )
    courses = TableSchema(
        name="courses",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="course_code", type="VARCHAR(20)", nullable=False),
            ColumnSchema(name="course_name", type="VARCHAR(150)", nullable=False),
            ColumnSchema(name="credits", type="INTEGER", nullable=False),
            ColumnSchema(name="department_id", type="INTEGER", nullable=True),
            ColumnSchema(name="faculty_id", type="INTEGER", nullable=True),
        ],
        primary_keys=["id"],
        source="live",
    )
    faculty = TableSchema(
        name="faculty",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="name", type="VARCHAR(100)", nullable=False),
            ColumnSchema(name="department_id", type="INTEGER", nullable=True),
            ColumnSchema(name="designation", type="VARCHAR(60)", nullable=True),
            ColumnSchema(name="salary", type="DECIMAL(10,2)", nullable=True),
        ],
        primary_keys=["id"],
        source="live",
    )
    fees = TableSchema(
        name="fees",
        columns=[
            ColumnSchema(name="id", type="BIGINT", nullable=False, autoincrement=True),
            ColumnSchema(name="student_id", type="INTEGER", nullable=False),
            ColumnSchema(name="fee_type", type="ENUM", nullable=False),
            ColumnSchema(name="amount", type="DECIMAL(10,2)", nullable=False),
            ColumnSchema(name="paid_amount", type="DECIMAL(10,2)", nullable=False),
            ColumnSchema(name="payment_status", type="ENUM", nullable=False),
        ],
        primary_keys=["id"],
        source="live",
    )
    marks = TableSchema(
        name="marks",
        columns=[
            ColumnSchema(name="id", type="BIGINT", nullable=False, autoincrement=True),
            ColumnSchema(name="enrollment_id", type="BIGINT", nullable=False),
            ColumnSchema(name="total_marks", type="DECIMAL(5,2)", nullable=False),
            ColumnSchema(name="result", type="ENUM", nullable=False),
        ],
        primary_keys=["id"],
        source="live",
    )
    enrollments = TableSchema(
        name="enrollments",
        columns=[
            ColumnSchema(name="id", type="BIGINT", nullable=False, autoincrement=True),
            ColumnSchema(name="student_id", type="INTEGER", nullable=False),
            ColumnSchema(name="course_id", type="INTEGER", nullable=False),
            ColumnSchema(name="semester_id", type="INTEGER", nullable=False),
        ],
        primary_keys=["id"],
        source="live",
    )
    semesters = TableSchema(
        name="semesters",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="academic_year", type="VARCHAR(9)", nullable=False),
            ColumnSchema(name="semester_no", type="INTEGER", nullable=False),
        ],
        primary_keys=["id"],
        source="live",
    )
    attendance = TableSchema(
        name="attendance",
        columns=[
            ColumnSchema(name="id", type="BIGINT", nullable=False, autoincrement=True),
            ColumnSchema(name="student_id", type="INTEGER", nullable=False),
            ColumnSchema(name="course_id", type="INTEGER", nullable=False),
            ColumnSchema(name="attendance_percent", type="DECIMAL(5,2)", nullable=False),
        ],
        primary_keys=["id"],
        source="live",
    )
    exam_schedules = TableSchema(
        name="exam_schedules",
        columns=[
            ColumnSchema(name="id", type="INTEGER", nullable=False, autoincrement=True),
            ColumnSchema(name="course_id", type="INTEGER", nullable=False),
            ColumnSchema(name="exam_type", type="ENUM", nullable=False),
            ColumnSchema(name="room_no", type="VARCHAR(20)", nullable=False),
        ],
        primary_keys=["id"],
        source="live",
    )
    return DatabaseSchema(
        database="college_large_demo",
        tables=[
            departments, students, courses, faculty, fees,
            marks, enrollments, semesters, attendance, exam_schedules,
        ],
        table_count=10,
    )


# ===========================================================================
# TASK 1 REGRESSION TESTS: Schema Availability
# ===========================================================================

class TestSchemaAvailabilityFixes:
    """Regression tests for all false-positive cases identified in the evaluation audit."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_college_schema):
        self.service = SchemaAvailabilityService()
        self.schema = mock_college_schema

    def test_q04_database_not_treated_as_table(self):
        """Q4: 'database' is an ordinary query noun, not a missing table."""
        res = self.service.analyze("How many students are in the database?", self.schema)
        assert res.is_available is True
        assert "students" in [t.name for t in res.relevant_tables]
        assert "database" not in res.missing_tables
        assert "databases" not in res.missing_tables

    def test_q06_that_not_treated_as_table(self):
        """Q6: 'that' is a conjunction/pronoun, not a missing table."""
        res = self.service.analyze("Show departments that have more than 500 students", self.schema)
        assert res.is_available is True
        assert "thats" not in res.missing_tables
        table_names = [t.name for t in res.relevant_tables]
        assert "departments" in table_names
        assert "students" in table_names

    def test_q07_along_not_treated_as_table(self):
        """Q7: 'along' is a preposition, not a missing table."""
        res = self.service.analyze("List all students along with their department name", self.schema)
        assert res.is_available is True
        assert "along" not in res.missing_tables
        assert "alongs" not in res.missing_tables
        table_names = [t.name for t in res.relevant_tables]
        assert "students" in table_names
        assert "departments" in table_names

    def test_q08_participles_not_treated_as_tables(self):
        """Q8: 'teaching', 'including', 'assigned' are verb/preposition forms, not tables."""
        question = "Show all courses and the name of the faculty teaching them, including courses with no assigned faculty"
        res = self.service.analyze(question, self.schema)
        assert res.is_available is True
        assert len(res.missing_tables) == 0
        table_names = [t.name for t in res.relevant_tables]
        assert "courses" in table_names
        assert "faculty" in table_names

    def test_q09_compound_academic_year_and_enrollments(self):
        """Q9: 'academic year' matches column academic_year, 'enrolled' matches enrollments."""
        question = "Show student names, their enrolled course names, and the semester academic year"
        res = self.service.analyze(question, self.schema)
        assert res.is_available is True
        assert len(res.missing_tables) == 0
        assert len(res.missing_columns) == 0

    def test_q10_higher_not_treated_as_table(self):
        """Q10: 'higher' is a comparative adjective, not a missing table."""
        res = self.service.analyze("Show students who have a CGPA higher than the average CGPA", self.schema)
        assert res.is_available is True
        assert "higher" not in res.missing_tables
        assert "highers" not in res.missing_tables

    def test_q13_offered_not_treated_as_table(self):
        """Q13: 'offered' is a past participle, not a missing table."""
        res = self.service.analyze("How many courses are offered by each department?", self.schema)
        assert res.is_available is True
        assert "offered" not in res.missing_tables
        assert "offereds" not in res.missing_tables

    def test_q14_members_and_sorting_words_not_treated_as_tables(self):
        """Q14: 'members', 'sorted', 'descending', 'order' are not missing tables."""
        question = "List all faculty members with their designation and department name, sorted by salary in descending order"
        res = self.service.analyze(question, self.schema)
        assert res.is_available is True
        assert len(res.missing_tables) == 0

    def test_q15_collected_and_pending_not_treated_as_tables(self):
        """Q15: 'collected', 'pending' are participles/adjectives, not tables."""
        res = self.service.analyze("Show total fees collected and total pending fees for each fee type", self.schema)
        assert res.is_available is True
        assert len(res.missing_tables) == 0
        assert "fees" in [t.name for t in res.relevant_tables]

    def test_q19_overall_and_higher_not_treated_as_tables(self):
        """Q19: 'higher' and 'overall' are adjectives, not missing tables."""
        res = self.service.analyze("Show departments where the average student CGPA is higher than the overall average CGPA", self.schema)
        assert res.is_available is True
        assert len(res.missing_tables) == 0

    def test_q20_compound_exam_schedules_and_room_number(self):
        """Q20: 'exam schedule' matches exam_schedules, 'room number' matches room_no."""
        question = "Show the exam schedule for Internal exams including course name and room number"
        res = self.service.analyze(question, self.schema)
        assert res.is_available is True
        assert len(res.missing_tables) == 0
        assert "exam_schedules" in [t.name for t in res.relevant_tables]

    def test_q21_male_female_not_treated_as_tables(self):
        """Q21: 'male' and 'female' are values/adjectives, not missing tables."""
        res = self.service.analyze("How many male and female students are there in each department?", self.schema)
        assert res.is_available is True
        assert len(res.missing_tables) == 0

    def test_q22_pending_and_due_not_treated_as_tables(self):
        """Q22: 'pending', 'due', 'payments' are not missing tables."""
        res = self.service.analyze("List students who have pending fee payments with the amount due", self.schema)
        assert res.is_available is True
        assert len(res.missing_tables) == 0
        table_names = [t.name for t in res.relevant_tables]
        assert "students" in table_names
        assert "fees" in table_names

    def test_q23_passed_failed_across_not_treated_as_tables(self):
        """Q23: 'passed', 'failed', 'across' are not missing tables."""
        res = self.service.analyze("How many students passed and how many failed across all courses?", self.schema)
        assert res.is_available is True
        assert len(res.missing_tables) == 0

    def test_q24_highest_and_members_not_treated_as_tables(self):
        """Q24: 'highest' and 'members' are not missing tables."""
        res = self.service.analyze("Which department has the highest number of faculty members?", self.schema)
        assert res.is_available is True
        assert len(res.missing_tables) == 0

    def test_missing_schema_clarification_preserved_for_unknown_entities(self):
        """Clarification feature remains active when an unknown domain table is requested."""
        res = self.service.analyze("Give me all employee details with salary", self.schema)
        # 'employee' is not in mock_college_schema
        assert res.is_available is False
        assert "employees" in res.missing_tables
        assert res.clarification_message is not None

    def test_missing_column_clarification_preserved(self):
        """Clarification feature remains active when requested column is missing across all tables."""
        # Using a small schema without salary
        small_schema = DatabaseSchema(
            database="small_db",
            tables=[self.schema.tables[0]],  # departments only (no salary)
            table_count=1,
        )
        res = self.service.analyze("Show all salaries", small_schema)
        assert res.is_available is False
        assert "salary" in res.missing_columns or "salaries" in res.missing_columns


# ===========================================================================
# TASK 2 REGRESSION TESTS: SQL Validator ORDER BY Aliases
# ===========================================================================

class TestSQLValidatorOrderByAlias:
    """Regression tests for ORDER BY alias validation in SQLValidatorService."""

    @pytest.fixture(autouse=True)
    def setup(self, mock_college_schema):
        self.validator = SQLValidatorService()
        self.schema = mock_college_schema

    def test_valid_select_alias_in_order_by(self):
        """Valid alias in SELECT list is allowed in ORDER BY."""
        sql = (
            "SELECT d.name AS department_name, COUNT(s.id) AS student_count "
            "FROM departments d LEFT JOIN students s ON d.id = s.department_id "
            "GROUP BY d.id, d.name ORDER BY student_count DESC;"
        )
        result = self.validator.validate(sql, self.schema)
        assert result.valid is True
        assert len(result.errors) == 0

    def test_valid_select_alias_with_backticks(self):
        """Aliases enclosed in backticks are valid in SELECT and ORDER BY."""
        sql = (
            "SELECT d.name AS `dept_name`, COUNT(s.id) AS `student_count` "
            "FROM departments d LEFT JOIN students s ON d.id = s.department_id "
            "GROUP BY d.id, d.name ORDER BY `student_count` DESC;"
        )
        result = self.validator.validate(sql, self.schema)
        assert result.valid is True
        assert len(result.errors) == 0

    def test_valid_select_alias_mixed_backticks(self):
        """Alias defined without backticks referenced in ORDER BY with backticks (and vice versa)."""
        sql1 = (
            "SELECT COUNT(s.id) AS student_count FROM students s "
            "ORDER BY `student_count` DESC;"
        )
        assert self.validator.validate(sql1, self.schema).valid is True

        sql2 = (
            "SELECT COUNT(s.id) AS `student_count` FROM students s "
            "ORDER BY student_count DESC;"
        )
        assert self.validator.validate(sql2, self.schema).valid is True

    def test_multiple_aliases_in_order_by(self):
        """Multiple SELECT aliases referenced in ORDER BY are all accepted."""
        sql = (
            "SELECT d.name AS department_name, COUNT(s.id) AS student_count, "
            "AVG(s.cgpa) AS avg_cgpa FROM departments d "
            "INNER JOIN students s ON d.id = s.department_id "
            "GROUP BY d.id, d.name ORDER BY student_count DESC, avg_cgpa ASC;"
        )
        result = self.validator.validate(sql, self.schema)
        assert result.valid is True

    def test_invalid_arbitrary_column_in_order_by_rejected(self):
        """Unknown column in ORDER BY is NOT allowed just because it's in ORDER BY."""
        sql = "SELECT name FROM students ORDER BY completely_fake_column DESC;"
        result = self.validator.validate(sql, self.schema)
        assert result.valid is False
        assert any("completely_fake_column" in err for err in result.errors)

    def test_qualified_unknown_column_in_order_by_rejected(self):
        """Table-qualified column in ORDER BY must exist on the table (aliases cannot be qualified)."""
        sql = (
            "SELECT d.name, COUNT(s.id) AS student_count FROM departments d "
            "LEFT JOIN students s ON d.id = s.department_id "
            "GROUP BY d.name ORDER BY s.student_count DESC;"
        )
        result = self.validator.validate(sql, self.schema)
        assert result.valid is False
        assert any("student_count" in err and "students" in err for err in result.errors)

    def test_subquery_alias_scoping_isolation(self):
        """An alias defined only inside a subquery cannot be used in the outer ORDER BY."""
        sql = (
            "SELECT name FROM students WHERE cgpa > ("
            "  SELECT AVG(cgpa) AS inner_avg FROM students"
            ") ORDER BY inner_avg DESC;"
        )
        result = self.validator.validate(sql, self.schema)
        assert result.valid is False
        assert any("inner_avg" in err for err in result.errors)

    def test_subquery_order_by_uses_its_own_alias(self):
        """An alias defined in a subquery is valid inside that subquery's own ORDER BY."""
        sql = (
            "SELECT name FROM ("
            "  SELECT name, cgpa AS score FROM students ORDER BY score DESC"
            ") AS sub;"
        )
        result = self.validator.validate(sql, self.schema)
        assert result.valid is True


    def test_alias_collision_with_existing_column(self):
        """When an alias collides with a real column name, it remains valid."""
        sql = "SELECT name AS name, cgpa AS cgpa FROM students ORDER BY name ASC;"
        result = self.validator.validate(sql, self.schema)
        assert result.valid is True

    def test_compound_union_order_by_alias(self):
        """ORDER BY at the end of a UNION can reference aliases from unioned SELECTs."""
        sql = (
            "SELECT name AS entity_name FROM students "
            "UNION "
            "SELECT name AS entity_name FROM faculty "
            "ORDER BY entity_name ASC;"
        )
        result = self.validator.validate(sql, self.schema)
        assert result.valid is True
