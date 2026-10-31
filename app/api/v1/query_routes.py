"""
Query generation route for /api/v1/query/generate.

Phase 3 — NL-to-SQL Generation.
Phase 4A — SQL Validation & Safety.

Accepts a natural-language question and returns a generated SQL query.
The SQL is produced by Gemini using the live database schema as context.

Phase 4A adds validation: the generated SQL is parsed and validated
against the live schema before being returned.  If validation fails,
a 422 error is returned with the validation errors.

IMPORTANT: The generated SQL is NOT executed against the database.
Execution belongs to a future phase.

Error handling follows the same pattern as Phase 2:
  - All errors return ErrorResponse (error_code, safe message, request_id).
  - Internal exception details are logged server-side only.
  - The Gemini API key is NEVER included in responses or logs.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from app.models.query_models import QueryGenerationRequest, QueryGenerationResponse
from app.models.response_models import ErrorResponse
from app.services.gemini_service import GeminiServiceError
from app.services.query_generation_service import QueryGenerationService
from app.services.schema_parser import SchemaParsingError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Query Generation v1"])


def _request_id(request: Request) -> str | None:
    """Extract the request ID from request state (set by RequestIDMiddleware)."""
    return getattr(request.state, "request_id", None)


@router.post(
    "/query/generate",
    response_model=QueryGenerationResponse,
    summary="Generate SQL from natural language (v1)",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request (empty question)"},
        422: {"model": ErrorResponse, "description": "Generated SQL failed validation or invalid schema metadata"},
        500: {"model": ErrorResponse, "description": "Schema retrieval or Gemini API failure"},
        503: {"model": ErrorResponse, "description": "Gemini API unavailable"},
    },
)
def generate_sql_query(
    body: QueryGenerationRequest,
    request: Request,
) -> QueryGenerationResponse:
    """Translate a natural-language question into a MySQL SELECT query using Gemini.

    Workflow:
      1. Inspects active schema (live DB + any user-provided temporary schema).
      2. If required tables or columns are missing, returns status='SCHEMA_REQUIRED'
         asking the user to supply the missing table schema.
      3. If required schema is available, displays schema metadata (metadata only, NO rows)
         and produces a validated MySQL SELECT query via Gemini.
      4. The generated SQL is returned to the caller. It is NOT executed automatically.

    Returns:
        200 QueryGenerationResponse on success (status='GENERATED' or status='SCHEMA_REQUIRED').
        400 ErrorResponse with error_code='INVALID_QUESTION' on validation failure.
        422 ErrorResponse with error_code='SQL_VALIDATION_FAILED' on SQL safety validation failure.
        422 ErrorResponse with error_code='INVALID_SCHEMA_METADATA' on invalid user schema.
        500 ErrorResponse with error_code='SCHEMA_RETRIEVAL_FAILED' on DB errors.
        503 ErrorResponse with error_code='SQL_GENERATION_FAILED' on Gemini errors.
    """
    rid = _request_id(request)

    try:
        service = QueryGenerationService()
        result = service.generate(
            question=body.question,
            user_schema=body.user_schema,
            user_schema_text=body.user_schema_text,
        )

    except SchemaParsingError as exc:
        logger.warning(
            "Invalid schema metadata during query generation [request_id=%s]: %s",
            rid,
            exc,
        )
        raise HTTPException(
            status_code=422,
            detail=ErrorResponse(
                error_code="INVALID_SCHEMA_METADATA",
                message=f"Invalid schema metadata: {exc}",
                request_id=rid,
            ).model_dump(),
        )

    except SQLAlchemyError as exc:
        logger.exception(
            "Schema retrieval failed during query generation [request_id=%s]: %s",
            rid,
            exc,
        )
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                error_code="SCHEMA_RETRIEVAL_FAILED",
                message="Failed to retrieve database schema for query generation. "
                        "Ensure the database is running.",
                request_id=rid,
            ).model_dump(),
        )

    except GeminiServiceError as exc:
        # GeminiServiceError.args[0] is always a safe user-facing message —
        # it never contains the API key or raw Gemini internals.
        safe_message = str(exc)
        logger.error(
            "Gemini generation failed [request_id=%s]: %s", rid, safe_message
        )
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                error_code="SQL_GENERATION_FAILED",
                message=safe_message,
                request_id=rid,
            ).model_dump(),
        )

    # If schema is required from user, return 200 with clarification instructions
    if result.status == "SCHEMA_REQUIRED":
        return QueryGenerationResponse(
            status="SCHEMA_REQUIRED",
            question=body.question,
            sql="",
            model=result.model,
            request_id=rid,
            validation=None,
            relevant_schemas=result.relevant_schemas,
            missing_schema=result.missing_schema,
            message=result.message,
            schema_source=result.schema_source,
        )

    # Phase 4A: If validation failed, return 422 with validation errors
    if result.validation and not result.validation.valid:
        logger.warning(
            "SQL validation failed [request_id=%s]: errors=%s",
            rid,
            result.validation.errors,
        )
        raise HTTPException(
            status_code=422,
            detail=ErrorResponse(
                error_code="SQL_VALIDATION_FAILED",
                message="Generated SQL failed safety validation: "
                        + "; ".join(result.validation.errors),
                request_id=rid,
            ).model_dump(),
        )

    return QueryGenerationResponse(
        status="GENERATED",
        question=body.question,
        sql=result.sql,
        model=result.model,
        request_id=rid,
        validation=result.validation,
        relevant_schemas=result.relevant_schemas,
        missing_schema=None,
        message=None,
        schema_source=result.schema_source,
    )

