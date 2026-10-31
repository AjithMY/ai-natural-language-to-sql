"""
API routes for database schema inspection.

Endpoints:
    GET /health          — Database connectivity health check
    GET /schema          — Full database schema
    GET /schema/{table}  — Schema for a single table
"""

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import verify_connection
from app.database.schema_manager import SchemaManager
from app.models.schema_models import DatabaseSchema, TableSchema

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", summary="Health check", tags=["Health"])
def health_check() -> dict:
    """Verify the API is running and the database is reachable."""
    if verify_connection():
        return {"status": "ok", "database": "connected"}

    raise HTTPException(
        status_code=503,
        detail={"status": "error", "database": "disconnected",
                "message": "Database is not reachable. Check your configuration."},
    )


@router.get("/schema", response_model=DatabaseSchema, summary="Full database schema", tags=["Schema"])
def get_full_schema() -> DatabaseSchema:
    """Return the complete schema of the connected database."""
    try:
        manager = SchemaManager()
        return manager.get_database_schema()
    except SQLAlchemyError:
        logger.exception("Error retrieving database schema")
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve database schema. Ensure the database is running and accessible.",
        )


@router.get("/schema/{table_name}", response_model=TableSchema,
            summary="Single table schema", tags=["Schema"])
def get_table_schema(table_name: str) -> TableSchema:
    """Return schema information for a specific table.

    Returns 404 if the table does not exist.
    """
    try:
        manager = SchemaManager()
        return manager.get_table_schema(table_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SQLAlchemyError:
        logger.exception("Error retrieving schema for table '%s'", table_name)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve schema for table '{table_name}'.",
        )
