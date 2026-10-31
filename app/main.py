"""
FastAPI application entry point.

Phase 1: Database Connection & Schema Management
Phase 2: Backend Foundation — service layer, API versioning, CORS, request IDs,
         global error handling, structured logging.
Phase 3: Gemini-Based NL-to-SQL Generation — accepts a natural-language question,
          retrieves the live database schema, and returns a generated MySQL SELECT
          query via the Gemini API.  The generated SQL is NOT executed.
Phase 5: Frontend / Dashboard — serves the static frontend files.

Route layout:
  Legacy (Phase 1, unchanged):
    GET /health
    GET /schema
    GET /schema/{table_name}

  Versioned (Phase 2):
    GET /api/v1/health
    GET /api/v1/schema
    GET /api/v1/schema/{table_name}

  Versioned (Phase 3):
    POST /api/v1/query/generate

  Frontend (Phase 5):
    GET /  → redirects to /frontend/index.html
    GET /frontend/{path}  → static files
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.schema_routes import router as schema_router      # Phase 1 (unchanged)
from app.api.v1 import v1_router                               # Phase 2 versioned routes
from app.core.config import settings
from app.middleware.request_id import RequestIDMiddleware
from app.models.response_models import ErrorResponse

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG if settings.APP_DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    description=(
        "Phase 1 — Database Connection & Schema Management.\n\n"
        "Phase 2 — Backend Foundation & API Architecture.\n\n"
        "Phase 3 — Gemini-Based NL-to-SQL Generation: accepts a natural-language "
        "question, retrieves the live database schema, and returns a generated MySQL "
        "SELECT query via the Gemini API. The generated SQL is NOT executed.\n\n"
        "Connects to a MySQL database and exposes structured schema information "
        "via REST endpoints."
    ),
)

# ---------------------------------------------------------------------------
# Middleware (outermost added last — RequestID runs first so request_id is
# available to all subsequent middleware, route handlers, and exception handlers)
# ---------------------------------------------------------------------------

# CORS — development origins configurable via CORS_ORIGINS env var.
# These defaults (localhost:3000, localhost:5173) are development-only.
# Update CORS_ORIGINS in .env before deploying to production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request ID — must be added AFTER CORSMiddleware so it becomes the outermost
# middleware and sets request.state.request_id before any other processing.
app.add_middleware(RequestIDMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

# Phase 1 legacy routes (kept exactly as-is so existing tests continue to pass)
app.include_router(schema_router)

# Phase 2 versioned routes under /api/v1/
app.include_router(v1_router)

# Phase 5: Frontend static files
_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/frontend", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    """Redirect the root URL to the frontend dashboard."""
    return RedirectResponse(url="/frontend/index.html")

# ---------------------------------------------------------------------------
# Global exception handler
#
# Catches unhandled exceptions that escape all route handlers.
# Only the safe ErrorResponse is returned to the client; full details
# are written to the server log for debugging.
#
# Note: HTTPException is handled by FastAPI's built-in handler and does NOT
# reach this handler, so Phase 1 routes that raise HTTPException are unaffected.
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a safe 500 response for any unhandled exception.

    The exception message is intentionally excluded from the response to
    prevent accidental exposure of internal details or credentials.
    """
    request_id: str | None = getattr(request.state, "request_id", None)
    logger.exception(
        "Unhandled exception on %s %s [request_id=%s]: %s",
        request.method,
        request.url.path,
        request_id,
        exc,
    )
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error_code="INTERNAL_ERROR",
            message="An unexpected error occurred. Please try again or contact support.",
            request_id=request_id,
        ).model_dump(),
    )


# ---------------------------------------------------------------------------
# Startup event
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup() -> None:
    """Log application startup information.

    Logs host and database name only.
    DB_PASSWORD is intentionally omitted from all log output.
    """
    logger.info(
        "Application starting: title='%s' version=%s environment=%s "
        "db_host=%s db_name=%s cors_origins=%s",
        settings.APP_TITLE,
        settings.APP_VERSION,
        settings.ENVIRONMENT,
        settings.DB_HOST,
        settings.DB_NAME,
        settings.CORS_ORIGINS,
    )
