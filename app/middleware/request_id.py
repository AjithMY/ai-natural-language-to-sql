"""
Request ID middleware.

Attaches a unique X-Request-ID to every request/response:
  - If the client sends X-Request-ID in the request headers, that value is echoed.
  - Otherwise a UUID4 is generated.

The request ID is stored on request.state.request_id so route handlers and
exception handlers can embed it in their responses for log correlation.

Implementation note:
  BaseHTTPMiddleware re-raises exceptions that escape from call_next before
  FastAPI's exception handlers can intercept them. To guarantee that
  unhandled exceptions still produce a safe JSON 500 response (with error_code
  and request_id) this middleware catches Exception as a last resort, logs it,
  and returns the structured error response itself.
"""

import logging
import uuid

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that injects a unique X-Request-ID into every request/response.

    Also acts as the final safety net for unhandled exceptions so that no raw
    Python tracebacks or internal details ever reach the client.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Honour a client-supplied ID so callers can correlate their own traces;
        # fall back to a fresh UUID when none is provided.
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        logger.info(
            "Request started: %s %s [request_id=%s]",
            request.method,
            request.url.path,
            request_id,
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            # This is the final safety net.  FastAPI's @app.exception_handler(Exception)
            # cannot intercept exceptions that bubble up through BaseHTTPMiddleware's
            # call_next, so we handle them here.  Internal details are logged
            # server-side only; the client receives a safe structured response.
            logger.exception(
                "Unhandled exception on %s %s [request_id=%s]: %s",
                request.method,
                request.url.path,
                request_id,
                exc,
            )
            return JSONResponse(
                status_code=500,
                headers={"X-Request-ID": request_id},
                content={
                    "error_code": "INTERNAL_ERROR",
                    "message": (
                        "An unexpected error occurred. "
                        "Please try again or contact support."
                    ),
                    "request_id": request_id,
                },
            )

        response.headers["X-Request-ID"] = request_id
        return response
