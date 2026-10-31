"""
Schema Manager — inspects the live MySQL database and returns structured
schema information using SQLAlchemy's inspection API.

This is an independent component. Future phases (NL-to-SQL, optimization)
will call the Schema Manager to understand database structure without
needing to implement their own inspection logic.
"""

import logging

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import get_engine
from app.core.config import settings
from app.models.schema_models import (
    ColumnSchema,
    DatabaseSchema,
    ForeignKeySchema,
    TableSchema,
    UniqueConstraintSchema,
)

logger = logging.getLogger(__name__)


class SchemaManager:
    """Inspects the connected MySQL database and returns structured schema data.

    Usage:
        manager = SchemaManager()
        full_schema = manager.get_database_schema()
        table_schema = manager.get_table_schema("students")
    """

    def __init__(self) -> None:
        self._engine = get_engine()

    def get_table_names(self) -> list[str]:
        """Return a list of all table names in the database."""
        try:
            inspector = inspect(self._engine)
            return inspector.get_table_names()
        except SQLAlchemyError as exc:
            logger.error("Failed to retrieve table names: %s", exc)
            raise

    def get_table_schema(self, table_name: str) -> TableSchema:
        """Return full schema information for a single table.

        Raises:
            ValueError: If the table does not exist.
            SQLAlchemyError: On database inspection errors.
        """
        inspector = inspect(self._engine)
        available_tables = inspector.get_table_names()

        if table_name not in available_tables:
            raise ValueError(f"Table '{table_name}' does not exist in database '{settings.DB_NAME}'.")

        # --- Columns ---
        columns: list[ColumnSchema] = []
        for col in inspector.get_columns(table_name):
            columns.append(
                ColumnSchema(
                    name=col["name"],
                    type=str(col["type"]),
                    nullable=col.get("nullable", True),
                    default=str(col["default"]) if col.get("default") is not None else None,
                    autoincrement=col.get("autoincrement", False),
                )
            )

        # --- Primary Keys ---
        pk_info = inspector.get_pk_constraint(table_name)
        primary_keys: list[str] = pk_info.get("constrained_columns", []) if pk_info else []

        # --- Foreign Keys ---
        foreign_keys: list[ForeignKeySchema] = []
        for fk in inspector.get_foreign_keys(table_name):
            # Each FK may reference multiple columns; handle the common single-column case
            local_cols = fk.get("constrained_columns", [])
            ref_cols = fk.get("referred_columns", [])
            ref_table = fk.get("referred_table", "")
            constraint_name = fk.get("name")

            for local_col, ref_col in zip(local_cols, ref_cols):
                foreign_keys.append(
                    ForeignKeySchema(
                        column=local_col,
                        references_table=ref_table,
                        references_column=ref_col,
                        constraint_name=constraint_name,
                    )
                )

        # --- Unique Constraints ---
        unique_constraints: list[UniqueConstraintSchema] = []
        for uc in inspector.get_unique_constraints(table_name):
            unique_constraints.append(
                UniqueConstraintSchema(
                    name=uc.get("name"),
                    columns=uc.get("column_names", []),
                )
            )

        return TableSchema(
            name=table_name,
            columns=columns,
            primary_keys=primary_keys,
            foreign_keys=foreign_keys,
            unique_constraints=unique_constraints,
        )

    def get_database_schema(self) -> DatabaseSchema:
        """Return schema information for the entire database."""
        table_names = self.get_table_names()
        tables: list[TableSchema] = []

        for name in table_names:
            try:
                tables.append(self.get_table_schema(name))
            except SQLAlchemyError as exc:
                logger.warning("Skipping table '%s' due to inspection error: %s", name, exc)

        return DatabaseSchema(
            database=settings.DB_NAME,
            tables=tables,
            table_count=len(tables),
        )
