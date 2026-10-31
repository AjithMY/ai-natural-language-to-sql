"""
Service layer for schema-related operations.

The service layer sits between API routes and the database layer.
It coordinates operations without implementing HTTP concerns (that belongs
to routes) or database inspection logic (that belongs to SchemaManager).

Current data flow:
    API Route → SchemaService → SchemaManager → MySQL

Future phases will extend this layer (NL-to-SQL, validation, optimization)
without touching existing route or database code.
"""

import logging

from app.database.schema_manager import SchemaManager
from app.models.schema_models import DatabaseSchema, TableSchema

logger = logging.getLogger(__name__)


class SchemaService:
    """Application service for schema-related operations.

    Wraps SchemaManager to provide a stable interface for routes.
    Future phases that extend schema functionality (e.g., caching schema
    for AI context) will do so here without modifying SchemaManager.
    """

    def __init__(self) -> None:
        self._manager = SchemaManager()

    def get_full_schema(self) -> DatabaseSchema:
        """Return the complete schema for the connected database.

        Raises:
            SQLAlchemyError: Propagated from SchemaManager on DB failure.
        """
        logger.debug("SchemaService: retrieving full database schema")
        return self._manager.get_database_schema()

    def get_table_schema(self, table_name: str) -> TableSchema:
        """Return schema for a single table.

        Raises:
            ValueError: If the table does not exist.
            SQLAlchemyError: On database inspection errors.
        """
        logger.debug("SchemaService: retrieving schema for table '%s'", table_name)
        return self._manager.get_table_schema(table_name)

    def get_table_names(self) -> list[str]:
        """Return a list of all table names in the connected database.

        Raises:
            SQLAlchemyError: On database inspection errors.
        """
        logger.debug("SchemaService: retrieving table names")
        return self._manager.get_table_names()
