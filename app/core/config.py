"""
Application configuration loaded from environment variables.

All database credentials are read from .env file at runtime.
No credentials are hardcoded anywhere in the source code.

Phase 2 additions:
  - CORS_ORIGINS   — allowed frontend origins (comma-separated in .env)
  - ENVIRONMENT    — 'development' or 'production'
  - APP_DEBUG      — enables extra debug logging when True

Phase 3 additions:
  - GEMINI_API_KEY — Google Gemini API key (NEVER log or expose to clients)
  - GEMINI_MODEL   — Gemini model name (e.g. gemini-3.6-flash)

Phase 4B additions:
  - SQL_MAX_ROWS           — maximum rows returned from query execution (default 1000)
  - SQL_QUERY_TIMEOUT_SECONDS — statement-level timeout in seconds (default 30)
"""

from urllib.parse import quote_plus

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # -------------------------------------------------------------------------
    # Database connection settings
    # -------------------------------------------------------------------------
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_NAME: str = "minor_project"
    DB_USER: str = "root"
    DB_PASSWORD: str = ""

    # -------------------------------------------------------------------------
    # Application settings
    # -------------------------------------------------------------------------
    APP_TITLE: str = "NL2SQL - Database Schema Manager"
    APP_VERSION: str = "1.0.0"

    # -------------------------------------------------------------------------
    # Phase 2: CORS, environment, debug flag
    # -------------------------------------------------------------------------

    # Allowed CORS origins for the frontend.
    # In .env, provide a comma-separated list:
    #   CORS_ORIGINS=http://localhost:3000,http://localhost:5173
    # These defaults are development-only and must be changed for production.
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8000",
    ]

    # Deployment environment — 'development' or 'production'.
    ENVIRONMENT: str = "development"

    # Set to True to enable DEBUG-level log output.
    APP_DEBUG: bool = False

    # -------------------------------------------------------------------------
    # Phase 3: Gemini AI configuration
    # -------------------------------------------------------------------------

    # Google Gemini API key — loaded exclusively from .env, never hardcoded.
    # IMPORTANT: Never log this value. Never include it in API responses.
    GEMINI_API_KEY: str = ""

    # Gemini model name. Configurable without touching application code.
    # See: https://ai.google.dev/gemini-api/docs/models
    GEMINI_MODEL: str = "gemini-flash-lite-latest"

    # -------------------------------------------------------------------------
    # Phase 4B: Safe SQL Execution
    # -------------------------------------------------------------------------

    # Maximum number of rows returned from query execution.
    # Prevents unbounded result sets from resource exhaustion.
    # Configurable via SQL_MAX_ROWS in .env.
    SQL_MAX_ROWS: int = 1000

    # Statement-level timeout in seconds for SQL execution.
    # Uses MySQL's max_execution_time optimizer hint.
    # Configurable via SQL_QUERY_TIMEOUT_SECONDS in .env.
    SQL_QUERY_TIMEOUT_SECONDS: int = 30

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: object) -> list[str]:
        """Accept either a comma-separated string or a JSON list from the env var."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v  # type: ignore[return-value]

    # -------------------------------------------------------------------------
    # Computed properties
    # -------------------------------------------------------------------------

    @property
    def database_url(self) -> str:
        """Build the SQLAlchemy MySQL connection URL.

        Uses PyMySQL as the MySQL driver.
        Credentials are URL-encoded to handle special characters (e.g. @ in passwords).

        IMPORTANT: Never log the return value of this property — it contains
        the database password in plain text.
        """
        user = quote_plus(self.DB_USER)
        password = quote_plus(self.DB_PASSWORD)
        return (
            f"mysql+pymysql://{user}:{password}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


# Singleton settings instance used throughout the application
settings = Settings()
