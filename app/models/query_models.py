"""
Pydantic request/response models for Phase 3: NL-to-SQL query generation.

These models define the contract for:
  POST /api/v1/query/generate

Design notes:
  - QueryGenerationRequest validates that the question is non-empty and
    non-whitespace (rejects "   " as well as "").
  - QueryGenerationResponse never exposes the Gemini API key or internal
    connection details.  It carries only the question, the generated SQL,
    the model name used, and the request_id for log correlation.
  - Generated SQL is NOT executed — it is returned as a plain string.
"""

from pydantic import BaseModel, Field, field_validator


from app.models.schema_models import TableSchema, UserProvidedSchema


class MissingSchemaInfo(BaseModel):
    """Details on required schema elements that are missing from the database."""

    missing_tables: list[str] = Field(default_factory=list, description="Tables required by question but missing")
    missing_columns: list[str] = Field(default_factory=list, description="Columns required by question but missing")
    available_tables: list[str] = Field(default_factory=list, description="Tables currently available in active schema")
    message: str = Field(default="", description="Human-readable clarification request message")


class QueryGenerationRequest(BaseModel):
    """Request body for POST /api/v1/query/generate.

    Example::

        {"question": "Show students who scored above 80"}
    """

    question: str = Field(
        ...,
        min_length=1,
        description="Natural-language database question (must not be empty or whitespace-only)",
        examples=["Show the top 5 students with the highest marks."],
    )
    user_schema: UserProvidedSchema | None = Field(
        default=None,
        description="Optional temporary schema provided by the user (structured)",
    )
    user_schema_text: str | None = Field(
        default=None,
        description="Optional temporary schema provided by the user in plain text/bullet format",
    )

    @field_validator("question")
    @classmethod
    def question_must_not_be_whitespace(cls, v: str) -> str:
        """Reject questions that consist entirely of whitespace."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("Question must not be empty or consist only of whitespace.")
        return stripped


class SQLValidationResult(BaseModel):
    """Validation result model for Phase 4: SQL Validation & Safety.

    Reports whether the SQL query passed validation checks (syntax, statement type,
    table and column existence against the schema, multi-statement checks).
    """

    valid: bool = Field(..., description="Whether the SQL query passed safety validation")
    sql: str = Field(..., description="The SQL query string that was validated")
    errors: list[str] = Field(
        default_factory=list,
        description="List of validation errors explaining why the query was rejected",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="List of non-fatal warnings or recommendations",
    )


class QueryGenerationResponse(BaseModel):
    """Response body for POST /api/v1/query/generate.

    The ``sql`` field contains the validated SQL string produced by Gemini.
    It is returned as-is — it is NEVER executed against the database.
    Relevant table schemas (metadata only, no rows) are returned in ``relevant_schemas``.

    Example::

        {
            "status": "GENERATED",
            "question": "Show students who scored above 80",
            "sql": "SELECT name, marks FROM students WHERE marks > 80;",
            "model": "gemini-3.6-flash",
            "request_id": "550e8400-e29b-41d4-a716-446655440000",
            "validation": {
                "valid": true,
                "sql": "SELECT name, marks FROM students WHERE marks > 80;",
                "errors": [],
                "warnings": []
            },
            "relevant_schemas": [...],
            "schema_source": "live"
        }
    """

    status: str = Field(default="GENERATED", description="Generation status: 'GENERATED' or 'SCHEMA_REQUIRED'")
    question: str = Field(..., description="The original natural-language question")
    sql: str = Field(default="", description="The generated SQL query (not executed)")
    model: str = Field(default="", description="Gemini model used for generation")
    request_id: str | None = Field(
        None,
        description="Request ID for correlating this response with server logs",
    )
    validation: SQLValidationResult | None = Field(
        None,
        description="SQL safety validation result",
    )
    relevant_schemas: list[TableSchema] = Field(
        default_factory=list,
        description="Schema metadata for all relevant tables (metadata only, strictly no table rows)",
    )
    missing_schema: MissingSchemaInfo | None = Field(
        None,
        description="Missing schema details when status is 'SCHEMA_REQUIRED'",
    )
    message: str | None = Field(
        None,
        description="Informational or clarification message for the user",
    )
    schema_source: str = Field(
        default="live",
        description="Source of schema used: 'live', 'user_provided', or 'combined'",
    )



# -------------------------------------------------------------------------
# Phase 4B: Safe SQL Execution models
# -------------------------------------------------------------------------


class QueryExecutionRequest(BaseModel):
    """Request body for POST /api/v1/query/execute.

    Example::

        {"sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;"}
    """

    sql: str = Field(
        ...,
        min_length=1,
        description="SQL query to validate and execute (must be a SELECT statement)",
        examples=["SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;"],
    )

    @field_validator("sql")
    @classmethod
    def sql_must_not_be_whitespace(cls, v: str) -> str:
        """Reject SQL that consists entirely of whitespace."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("SQL must not be empty or consist only of whitespace.")
        return stripped


class QueryExecutionResponse(BaseModel):
    """Response body for POST /api/v1/query/execute.

    Returns structured execution results with column names and rows.
    NULL values are preserved as ``None`` in the JSON output.

    Example::

        {
            "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;",
            "columns": ["name", "marks"],
            "rows": [["Alice", 95], ["Bob", 91]],
            "row_count": 2,
            "truncated": false,
            "max_rows": 1000,
            "validation": {"valid": true, "sql": "...", "errors": [], "warnings": []},
            "request_id": "550e8400-e29b-41d4-a716-446655440000"
        }
    """

    sql: str = Field(..., description="The executed SQL query")
    columns: list[str] = Field(..., description="Column names in the result set")
    rows: list[list] = Field(
        ...,
        description="Result rows — each row is a list of values (NULL → None)",
    )
    row_count: int = Field(..., description="Number of rows returned")
    truncated: bool = Field(
        ...,
        description="True if the result set was truncated to max_rows",
    )
    max_rows: int = Field(
        ...,
        description="Configured maximum rows limit that was applied",
    )
    validation: SQLValidationResult = Field(
        ...,
        description="SQL safety validation result",
    )
    request_id: str | None = Field(
        None,
        description="Request ID for correlating this response with server logs",
    )


# -------------------------------------------------------------------------
# Phase 4C: MySQL Query Plan Analysis models
# -------------------------------------------------------------------------


class ExplainPlanRow(BaseModel):
    """One row of MySQL EXPLAIN output.

    Every field except ``id`` may be NULL depending on the query and
    MySQL version.  Fields are typed to match the MySQL EXPLAIN column
    semantics as closely as possible.

    Reference: https://dev.mysql.com/doc/refman/8.0/en/explain-output.html
    """

    id: int | None = Field(None, description="SELECT identifier (query block number)")
    select_type: str | None = Field(None, description="Type of SELECT (SIMPLE, PRIMARY, SUBQUERY, …)")
    table: str | None = Field(None, description="Table name referenced in this plan step")
    partitions: str | None = Field(None, description="Matched partitions (NULL if not partitioned)")
    type: str | None = Field(None, description="Join/access type (ALL, index, range, ref, eq_ref, const, …)")
    possible_keys: str | None = Field(None, description="Comma-separated list of candidate indexes")
    key: str | None = Field(None, description="Index actually chosen by the optimizer")
    key_len: str | None = Field(None, description="Length of the chosen key (bytes)")
    ref: str | None = Field(None, description="Columns or constants compared to the index")
    rows: int | None = Field(None, description="Estimated number of rows to examine")
    filtered: float | None = Field(None, description="Percentage of rows filtered by the table condition")
    extra: str | None = Field(None, description="Additional information (Using filesort, Using temporary, …)")


class QueryExplainRequest(BaseModel):
    """Request body for POST /api/v1/query/explain.

    Example::

        {"sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;"}
    """

    sql: str = Field(
        ...,
        min_length=1,
        description="SQL query to validate and obtain the EXPLAIN plan for (must be a SELECT statement)",
        examples=["SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;"],
    )

    @field_validator("sql")
    @classmethod
    def sql_must_not_be_whitespace(cls, v: str) -> str:
        """Reject SQL that consists entirely of whitespace."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("SQL must not be empty or consist only of whitespace.")
        return stripped


class QueryExplainResponse(BaseModel):
    """Response body for POST /api/v1/query/explain.

    Returns the MySQL EXPLAIN plan rows and a list of descriptive
    observations derived from the plan.  The original SELECT is
    NOT executed — only ``EXPLAIN SELECT …`` is sent to MySQL.

    Example::

        {
            "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;",
            "plan": [
                {
                    "id": 1,
                    "select_type": "SIMPLE",
                    "table": "students",
                    "type": "ALL",
                    "possible_keys": null,
                    "key": null,
                    "rows": 3,
                    "filtered": 100.0,
                    "extra": "Using filesort"
                }
            ],
            "analysis": [
                "Full table scan on 'students'",
                "Using filesort",
                "3 estimated rows scanned on 'students'"
            ],
            "validation": {"valid": true, "sql": "…", "errors": [], "warnings": []},
            "request_id": "550e8400-…"
        }
    """

    sql: str = Field(..., description="The SQL query that was analyzed")
    plan: list[ExplainPlanRow] = Field(
        ...,
        description="MySQL EXPLAIN plan rows",
    )
    analysis: list[str] = Field(
        default_factory=list,
        description="Descriptive observations derived from the EXPLAIN plan",
    )
    validation: SQLValidationResult = Field(
        ...,
        description="SQL safety validation result",
    )
    request_id: str | None = Field(
        None,
        description="Request ID for correlating this response with server logs",
    )


# -------------------------------------------------------------------------
# Phase 4D: SQL Query Optimization models
# -------------------------------------------------------------------------


class OptimizationApplied(BaseModel):
    """Describes a single optimization rule that was applied.

    Each entry records the rule name, a human-readable description,
    and optionally the original and optimized SQL fragments that changed.
    """

    rule: str = Field(..., description="Machine-readable optimization rule name")
    description: str = Field(..., description="Human-readable description of the optimization")
    original_fragment: str | None = Field(
        None, description="The original SQL fragment before optimization"
    )
    optimized_fragment: str | None = Field(
        None, description="The optimized SQL fragment after optimization"
    )


class QueryOptimizeRequest(BaseModel):
    """Request body for POST /api/v1/query/optimize.

    Example::

        {"sql": "SELECT * FROM students;"}
    """

    sql: str = Field(
        ...,
        min_length=1,
        description="SQL query to validate and optimize (must be a SELECT statement)",
        examples=["SELECT * FROM students ORDER BY marks DESC;"],
    )

    @field_validator("sql")
    @classmethod
    def sql_must_not_be_whitespace(cls, v: str) -> str:
        """Reject SQL that consists entirely of whitespace."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("SQL must not be empty or consist only of whitespace.")
        return stripped


class QueryOptimizeResponse(BaseModel):
    """Response body for POST /api/v1/query/optimize.

    Returns the original SQL alongside the optimized candidate.  If no
    safe optimization was applicable, ``changed`` is False and
    ``optimized_sql`` equals the original.

    Neither the original nor the optimized SQL is executed by this
    endpoint — this phase produces a candidate only.

    Example::

        {
            "original_sql": "SELECT * FROM students;",
            "optimized_sql": "SELECT id, name, email, marks, … FROM students;",
            "changed": true,
            "optimizations": [
                {
                    "rule": "select_star_expansion",
                    "description": "Expanded SELECT * …"
                }
            ],
            "index_recommendations": [],
            "validation": {"valid": true, …},
            "optimized_validation": {"valid": true, …},
            "request_id": "…"
        }
    """

    original_sql: str = Field(..., description="The original SQL query before optimization")
    optimized_sql: str = Field(
        ...,
        description="The optimized SQL candidate (equals original if no optimization applied)",
    )
    changed: bool = Field(
        ..., description="True if the optimized SQL differs from the original"
    )
    optimizations: list[OptimizationApplied] = Field(
        default_factory=list,
        description="List of optimization rules that were applied",
    )
    index_recommendations: list[str] = Field(
        default_factory=list,
        description="Index-related observations/recommendations (no indexes are created)",
    )
    validation: SQLValidationResult = Field(
        ..., description="Phase 4A validation result for the ORIGINAL SQL"
    )
    optimized_validation: SQLValidationResult | None = Field(
        None,
        description="Phase 4A validation result for the OPTIMIZED SQL (None if unchanged)",
    )
    request_id: str | None = Field(
        None,
        description="Request ID for correlating this response with server logs",
    )


# -------------------------------------------------------------------------
# Phase 4E: Query Performance Analysis models
# -------------------------------------------------------------------------


class QueryPerformanceRequest(BaseModel):
    """Request body for POST /api/v1/query/performance.

    Benchmarks a validated SELECT query by executing it multiple times
    and returning aggregate timing statistics.

    Example::

        {
            "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;",
            "run_count": 5
        }
    """

    sql: str = Field(
        ...,
        min_length=1,
        description="SQL query to validate and benchmark (must be a SELECT statement)",
        examples=["SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;"],
    )
    run_count: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of timed benchmark runs (1–20, default 5). "
                    "Warmup runs, if any, are additional and excluded from statistics.",
    )

    @field_validator("sql")
    @classmethod
    def sql_must_not_be_whitespace(cls, v: str) -> str:
        """Reject SQL that consists entirely of whitespace."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("SQL must not be empty or consist only of whitespace.")
        return stripped


class QueryPerformanceResponse(BaseModel):
    """Response body for POST /api/v1/query/performance.

    Returns per-run timings and aggregate statistics (all in milliseconds).
    Warmup runs, if performed, are NOT included in these statistics.

    Example::

        {
            "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;",
            "run_count": 5,
            "warmup_runs": 1,
            "timings_ms": [2.31, 2.18, 2.25, 2.20, 2.22],
            "average_ms": 2.232,
            "median_ms": 2.22,
            "min_ms": 2.18,
            "max_ms": 2.31,
            "validation": {"valid": true, ...},
            "request_id": "550e8400-…"
        }
    """

    sql: str = Field(..., description="The SQL query that was benchmarked")
    run_count: int = Field(..., description="Number of timed benchmark runs performed")
    warmup_runs: int = Field(
        default=0,
        description="Number of warmup runs performed (excluded from statistics)",
    )
    timings_ms: list[float] = Field(
        ...,
        description="Individual execution times in milliseconds (one per timed run)",
    )
    average_ms: float = Field(..., description="Arithmetic mean of all timed runs (ms)")
    median_ms: float = Field(..., description="Median of all timed runs (ms)")
    min_ms: float = Field(..., description="Minimum execution time across all timed runs (ms)")
    max_ms: float = Field(..., description="Maximum execution time across all timed runs (ms)")
    validation: SQLValidationResult = Field(
        ...,
        description="SQL safety validation result",
    )
    request_id: str | None = Field(
        None,
        description="Request ID for correlating this response with server logs",
    )
