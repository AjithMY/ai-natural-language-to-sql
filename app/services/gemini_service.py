"""
Gemini service for Phase 3: NL-to-SQL generation.

Responsibilities:
  - Initialize the google-genai client using GEMINI_API_KEY from settings.
  - Accept a prompt string.
  - Send the prompt to the configured Gemini model.
  - Strip Markdown code fences from the response (Gemini sometimes adds them
    despite instructions).
  - Handle API errors (missing key, network failure, rate limit, empty response)
    and raise descriptive application-level exceptions.

This service intentionally has NO dependency on the database layer.
It does NOT:
  - Connect to MySQL.
  - Execute SQL.
  - Validate SQL syntax or semantics.
  - Optimize SQL.

Security note:
  - The API key is NEVER logged at any level.
  - The API key is NEVER included in raised exceptions or their messages.
  - All Gemini errors are caught, logged with safe messages, and re-raised
    as GeminiServiceError for the route layer to handle.
"""

import logging
import re

from google import genai
from google.genai import errors as genai_errors

from app.core.config import settings

logger = logging.getLogger(__name__)


class GeminiServiceError(Exception):
    """Raised when the Gemini service encounters a recoverable error.

    The message is safe to include in API error responses — it contains
    no credentials or internal details.
    """


class GeminiService:
    """Sends prompts to the Gemini API and returns the generated SQL text.

    Usage::

        service = GeminiService()
        sql = service.generate_sql(prompt)
    """

    def __init__(self) -> None:
        """Initialize the Gemini client.

        Raises:
            GeminiServiceError: If GEMINI_API_KEY is not configured.
        """
        api_key = settings.GEMINI_API_KEY
        if not api_key or api_key.strip() == "" or api_key == "your_gemini_api_key_here":
            raise GeminiServiceError(
                "Gemini API key is not configured. "
                "Set GEMINI_API_KEY in your .env file."
            )
        # The API key is passed directly to the client and is never logged.
        self._client = genai.Client(api_key=api_key)
        self._model = settings.GEMINI_MODEL

    def generate_sql(self, prompt: str) -> str:
        """Send a prompt to Gemini and return the cleaned SQL response.

        Args:
            prompt: The fully assembled prompt (schema + instructions + question).

        Returns:
            The generated SQL string, with Markdown fences stripped.

        Raises:
            GeminiServiceError: On any API failure, timeout, rate limit, or
                empty/unrecognised response.  The exception message is safe
                to surface to API clients.
        """
        logger.info(
            "Sending prompt to Gemini [model=%s, prompt_length=%d chars]",
            self._model,
            len(prompt),
        )

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
            )
        except genai_errors.ClientError as exc:
            # Covers 4xx errors (bad request, invalid API key, quota exceeded, etc.)
            logger.error(
                "Gemini client error [model=%s]: %s", self._model, type(exc).__name__
            )
            raise GeminiServiceError(
                "Gemini API returned a client error. "
                "Check your API key and request parameters."
            ) from exc
        except genai_errors.ServerError as exc:
            # Covers 5xx server-side errors
            logger.error(
                "Gemini server error [model=%s]: %s", self._model, type(exc).__name__
            )
            raise GeminiServiceError(
                "Gemini API is temporarily unavailable. Please try again later."
            ) from exc
        except Exception as exc:
            # Network errors, timeouts, unexpected SDK errors
            logger.error(
                "Unexpected Gemini error [model=%s]: %s",
                self._model,
                type(exc).__name__,
            )
            raise GeminiServiceError(
                "An unexpected error occurred while contacting the Gemini API. "
                "Please try again."
            ) from exc

        # Extract text from the response
        raw_text: str | None = None
        try:
            raw_text = response.text
        except Exception:
            raw_text = None

        if not raw_text or not raw_text.strip():
            logger.warning(
                "Gemini returned an empty or unrecognisable response [model=%s]",
                self._model,
            )
            raise GeminiServiceError(
                "Gemini returned an empty response. "
                "Please rephrase your question and try again."
            )

        sql = self._strip_markdown_fences(raw_text.strip())
        logger.info(
            "Gemini response received [model=%s, sql_length=%d chars]",
            self._model,
            len(sql),
        )
        return sql

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        """Remove Markdown SQL code fences from the model output.

        Handles both ``````sql`` and plain ``````  fences.
        If no fences are present, the text is returned unchanged.

        Examples::

            ```sql
            SELECT * FROM students;
            ```
            →  "SELECT * FROM students;"

            ```
            SELECT * FROM students;
            ```
            →  "SELECT * FROM students;"

            SELECT * FROM students;   →  "SELECT * FROM students;"
        """
        # Match optional language identifier after opening fence
        pattern = re.compile(
            r"^```(?:sql)?\s*\n?(.*?)\n?```\s*$",
            re.DOTALL | re.IGNORECASE,
        )
        match = pattern.match(text)
        if match:
            return match.group(1).strip()
        return text
