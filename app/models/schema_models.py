"""
Pydantic models representing database schema structure.

These models define the shape of all schema-related API responses.
They are intentionally decoupled from SQLAlchemy ORM models so that
future phases (e.g., NL-to-SQL generation) can consume them directly
without depending on a live database connection.
"""

from pydantic import BaseModel, Field


class ColumnSchema(BaseModel):
    """Schema representation of a single database column."""

    name: str = Field(..., description="Column name")
    type: str = Field(..., description="Column data type (e.g. INTEGER, VARCHAR)")
    nullable: bool = Field(..., description="Whether the column accepts NULL values")
    default: str | None = Field(None, description="Default value, if any")
    autoincrement: bool = Field(False, description="Whether the column auto-increments")


class ForeignKeySchema(BaseModel):
    """Schema representation of a foreign key constraint."""

    column: str = Field(..., description="Local column name")
    references_table: str = Field(..., description="Referenced table name")
    references_column: str = Field(..., description="Referenced column name")
    constraint_name: str | None = Field(None, description="Name of the FK constraint")


class UniqueConstraintSchema(BaseModel):
    """Schema representation of a unique constraint."""

    name: str | None = Field(None, description="Constraint name")
    columns: list[str] = Field(..., description="Columns in the unique constraint")


class TableSchema(BaseModel):
    """Complete schema representation of a single database table."""

    name: str = Field(..., description="Table name")
    columns: list[ColumnSchema] = Field(default_factory=list, description="List of columns")
    primary_keys: list[str] = Field(default_factory=list, description="Primary key column names")
    foreign_keys: list[ForeignKeySchema] = Field(default_factory=list, description="Foreign key constraints")
    unique_constraints: list[UniqueConstraintSchema] = Field(
        default_factory=list, description="Unique constraints"
    )
    source: str = Field(default="live", description="Schema source: 'live' or 'user_provided'")


class DatabaseSchema(BaseModel):
    """Complete schema representation of the connected database."""

    database: str = Field(..., description="Database name")
    tables: list[TableSchema] = Field(default_factory=list, description="All tables in the database")
    table_count: int = Field(0, description="Total number of tables")


class UserColumnInput(BaseModel):
    """User-provided column definition for temporary schema."""

    name: str = Field(..., min_length=1, max_length=64, description="Column name")
    type: str = Field(..., min_length=1, max_length=64, description="Column data type (e.g., INT, VARCHAR(100))")
    nullable: bool = Field(default=True, description="Whether the column accepts NULL values")
    primary_key: bool = Field(default=False, description="Whether this column is a primary key")
    default: str | None = Field(default=None, description="Default value if any")


class UserForeignKeyInput(BaseModel):
    """User-provided foreign key relationship."""

    column: str = Field(..., min_length=1, max_length=64, description="Local column name")
    references_table: str = Field(..., min_length=1, max_length=64, description="Referenced table name")
    references_column: str = Field(..., min_length=1, max_length=64, description="Referenced column name")
    constraint_name: str | None = Field(default=None, description="Optional constraint name")


class UserTableInput(BaseModel):
    """User-provided table schema for SQL generation."""

    name: str = Field(..., min_length=1, max_length=64, description="Table name")
    columns: list[UserColumnInput] = Field(..., min_length=1, description="List of columns")
    primary_keys: list[str] = Field(default_factory=list, description="Primary key column names")
    foreign_keys: list[UserForeignKeyInput] = Field(default_factory=list, description="Foreign key relationships")


class UserProvidedSchema(BaseModel):
    """Container for temporary user-provided schema definitions."""

    tables: list[UserTableInput] = Field(default_factory=list, description="User-provided tables")

