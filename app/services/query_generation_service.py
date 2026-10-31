"""
Query generation service for Phase 3: NL-to-SQL generation.
Updated in Phase 4A: SQL validation integrated.
Updated in Schema Clarification Flow: Missing schema detection, user temporary schemas,
and relevant schema metadata display without data.

Orchestrates the complete NL-to-SQL workflow:

  Route
    → QueryGenerationService.generate(question, user_schema, user_schema_text)
        → SchemaService.get_full_schema()       (live database schema)
        → SchemaParser.parse()                  (parse & validate user schema)
        → Combine live + temporary schema       (in-memory DatabaseSchema)
        → SchemaAvailabilityService.analyze()   (check if required schema present)
        → If missing schema:
            ← returns QueryGenerationResult(status="SCHEMA_REQUIRED", missing_schema=...)
        → If schema present:
            → PromptBuilder.build_prompt()      (combined schema + instructions + question)
            → GeminiService.generate_sql()      (Gemini API call)
            → SQLValidatorService.validate()    (Phase 4A: AST-based safety check against combined schema)
            ← returns QueryGenerationResult(status="GENERATED", sql=..., relevant_schemas=...)

HARD CONSTRAINT:
  This service MUST NOT execute the generated SQL.
  The SQL string is passed through as-is to the API route.
  Execution belongs to a future phase and must only occur on explicit user request.
"""

from dataclasses import dataclass
import logging
from typing import Iterator

from app.core.config import settings
from app.models.query_models import MissingSchemaInfo, SQLValidationResult
from app.models.schema_models import DatabaseSchema, TableSchema, UserProvidedSchema
from app.services.gemini_service import GeminiService, GeminiServiceError
from app.services.prompt_builder import PromptBuilder
from app.services.schema_availability_service import SchemaAvailabilityService
from app.services.schema_parser import SchemaParser, SchemaParsingError
from app.services.schema_service import SchemaService
from app.services.sql_validator import SQLValidatorService

logger = logging.getLogger(__name__)


@dataclass
class QueryGenerationResult:
    """Result returned by QueryGenerationService.

    Supports tuple unpacking: ``sql, model, validation = service.generate(...)``
    for complete backward compatibility with Phase 3/4A callers.
    """

    status: str
    sql: str
    model: str
    validation: SQLValidationResult | None
    relevant_schemas: list[TableSchema]
    missing_schema: MissingSchemaInfo | None = None
    message: str | None = None
    schema_source: str = "live"

    def __iter__(self) -> Iterator:
        """Allow tuple unpacking (sql, model, validation) for backward compatibility."""
        return iter((self.sql, self.model, self.validation))


class QueryGenerationService:
    """Orchestrates natural-language-to-SQL generation with schema availability checking and validation.

    Composes SchemaService, SchemaParser, SchemaAvailabilityService, PromptBuilder,
    GeminiService, and SQLValidatorService.

    Usage::

        service = QueryGenerationService()
        result = service.generate("Show students with marks above 80")
        # Or legacy unpacking:
        sql, model, validation = service.generate("Show students with marks above 80")
    """

    def __init__(self) -> None:
        self._schema_service = SchemaService()
        self._schema_parser = SchemaParser()
        self._schema_availability = SchemaAvailabilityService()
        self._prompt_builder = PromptBuilder()
        self._gemini_service = GeminiService()
        self._sql_validator = SQLValidatorService()

    def generate(
        self,
        question: str,
        user_schema: UserProvidedSchema | None = None,
        user_schema_text: str | None = None,
    ) -> QueryGenerationResult:
        """Generate a SQL query from a natural-language question.

        Workflow:
          1. Retrieve the live database schema via SchemaService.
          2. Parse and validate user-provided temporary schema (if supplied).
          3. Combine live and temporary schema in memory.
          4. Check schema availability for the question.
             - If required schema is missing: return status='SCHEMA_REQUIRED' with missing info.
          5. Build a Gemini prompt containing the combined schema + question.
          6. Send the prompt to GeminiService.
          7. Validate the generated SQL against the combined schema (Phase 4A).
          8. Return the generated SQL, model name, validation result, and relevant table schemas.

        The generated SQL is NOT executed.

        Args:
            question: The user's natural-language question.
            user_schema: Optional structured temporary schema provided by the user.
            user_schema_text: Optional plain-text or bulleted temporary schema.

        Returns:
            A QueryGenerationResult object (also unpackable as (sql, model_name, validation_result)).

        Raises:
            SQLAlchemyError: If schema retrieval fails (propagated from SchemaService).
            GeminiServiceError: If Gemini generation fails (propagated from GeminiService).
            SchemaParsingError: If user-provided schema is invalid or malicious.
        """
        logger.info("QueryGenerationService: starting generation for question [length=%d]", len(question))

        # Step 1: Retrieve the real database schema (metadata only, no rows)
        logger.debug("QueryGenerationService: retrieving database schema")
        live_schema = self._schema_service.get_full_schema()

        # Step 2: Parse user-provided temporary schema if present
        user_tables: list[TableSchema] = []
        if user_schema or (user_schema_text and user_schema_text.strip()):
            logger.info("QueryGenerationService: parsing user-provided temporary schema")
            user_tables = self._schema_parser.parse(user_schema, user_schema_text)

        # Step 3: Combine live schema + user temporary schema in memory
        combined_schema, schema_source = self._combine_schemas(live_schema, user_tables)
        logger.debug(
            "QueryGenerationService: combined schema ready [tables=%d, source=%s]",
            combined_schema.table_count,
            schema_source,
        )

        # Step 4: Schema availability analysis
        analysis = self._schema_availability.analyze(question, combined_schema)

        if not analysis.is_available:
            logger.info(
                "QueryGenerationService: schema missing for question [missing_tables=%s, missing_columns=%s]",
                analysis.missing_tables,
                analysis.missing_columns,
            )
            return QueryGenerationResult(
                status="SCHEMA_REQUIRED",
                sql="",
                model=settings.GEMINI_MODEL,
                validation=None,
                relevant_schemas=analysis.relevant_tables,
                missing_schema=analysis.missing_info,
                message=analysis.clarification_message,
                schema_source=schema_source,
            )

        # Step 5: Build the prompt using combined schema
        logger.debug("QueryGenerationService: building Gemini prompt")
        prompt = self._prompt_builder.build_prompt(combined_schema, question)

        # Step 6: Generate SQL via Gemini (NOT executed)
        logger.debug("QueryGenerationService: calling GeminiService")
        sql = self._gemini_service.generate_sql(prompt)

        logger.info(
            "QueryGenerationService: SQL generated successfully [sql_length=%d chars]",
            len(sql),
        )

        # Step 7: Validate the generated SQL against combined schema (Phase 4A)
        logger.debug("QueryGenerationService: validating generated SQL")
        validation_result = self._sql_validator.validate(sql, combined_schema)

        if validation_result.valid:
            logger.info("QueryGenerationService: SQL validation PASSED")
        else:
            logger.warning(
                "QueryGenerationService: SQL validation FAILED [errors=%s]",
                validation_result.errors,
            )

        # Step 8: Return the result (NOT executed)
        return QueryGenerationResult(
            status="GENERATED",
            sql=sql,
            model=settings.GEMINI_MODEL,
            validation=validation_result,
            relevant_schemas=analysis.relevant_tables,
            missing_schema=None,
            message=None,
            schema_source=schema_source,
        )

    def _combine_schemas(
        self,
        live_schema: DatabaseSchema,
        user_tables: list[TableSchema],
    ) -> tuple[DatabaseSchema, str]:
        """Combine live DatabaseSchema with user-provided temporary TableSchema objects in memory.

        Returns a tuple of (combined_schema, schema_source).
        """
        if not user_tables:
            return live_schema, "live"

        tables: list[TableSchema] = list(live_schema.tables)
        existing_names = {t.name.lower(): i for i, t in enumerate(tables)}

        for ut in user_tables:
            ut_lower = ut.name.lower()
            if ut_lower in existing_names:
                # Override or merge table definition with user-provided metadata
                tables[existing_names[ut_lower]] = ut
            else:
                tables.append(ut)
                existing_names[ut_lower] = len(tables) - 1

        combined = DatabaseSchema(
            database=live_schema.database,
            tables=tables,
            table_count=len(tables),
        )

        has_live = bool(live_schema.tables)
        source = "combined" if has_live else "user_provided"
        return combined, source

