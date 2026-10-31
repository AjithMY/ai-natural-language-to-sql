"""
Database connection management using SQLAlchemy.

Provides a reusable engine and connection verification utility.
This module is the single point of contact for database connectivity.
"""

import logging

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.core.config import settings

logger = logging.getLogger(__name__)

# Module-level engine (created once, reused across the application)
_engine: Engine | None = None


def get_engine() -> Engine:
    """Return the SQLAlchemy engine, creating it on first call.

    The engine manages a connection pool internally, so creating it
    once and reusing it is the recommended SQLAlchemy pattern.
    """
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,  # Verify connections before using them
            pool_size=5,
            max_overflow=10,
            pool_recycle=3600,   # Recycle connections after 1 hour
            echo=False,
        )
        logger.info("Database engine created for host=%s, db=%s",
                     settings.DB_HOST, settings.DB_NAME)
    return _engine


def verify_connection() -> bool:
    """Check whether the database is reachable.

    Returns True if a simple query succeeds, False otherwise.
    Errors are logged but never expose credentials.
    """
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        logger.error("Database connection failed — check host, port, credentials, and database name.")
        return False
    except SQLAlchemyError:
        logger.error("Unexpected database error during connection check.")
        return False


def reset_engine() -> None:
    """Dispose of the current engine (useful for testing or reconfiguration)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
        logger.info("Database engine disposed.")
