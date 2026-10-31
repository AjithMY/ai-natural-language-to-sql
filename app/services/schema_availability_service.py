"""
Schema Availability Service.

Analyzes natural-language questions against the active database schema to:
  1. Identify all relevant tables required for the query.
  2. Detect missing tables or columns before sending to Gemini or generating SQL.
  3. Ensure application logic, not the LLM, validates schema existence against the live database.
  4. Prepare structured schema metadata for relevant tables (METADATA ONLY, strictly no row data).
"""

import logging
import re
from dataclasses import dataclass, field

from app.models.query_models import MissingSchemaInfo
from app.models.schema_models import DatabaseSchema, TableSchema

logger = logging.getLogger(__name__)

# Common English stopwords, question words, SQL terms, and verb forms that do NOT represent domain tables/columns
STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "by", "for", "with", "about", "against",
    "between", "into", "through", "during", "before", "after", "above", "below",
    "to", "from", "up", "down", "of", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how", "all", "any",
    "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "can", "will",
    "just", "should", "now", "show", "list", "give", "me", "find", "get", "display",
    "fetch", "select", "who", "which", "what", "whose", "whom", "have", "has",
    "had", "having", "do", "does", "did", "doing", "is", "am", "are", "was",
    "were", "be", "been", "being", "details", "data", "record", "records", "row",
    "rows", "table", "information", "info", "per", "every", "many", "count",
    "sum", "average", "avg", "max", "min", "top", "bottom", "highest", "lowest",
    "greater", "less", "equal", "than", "number", "total", "and", "or",
    "nonexistent", "fake", "dummy", "random", "sample", "unknown", "invalid", "bad", "test",
    "scored", "scores", "scoring", "score", "enrolled", "enrolls", "enrolling", "enroll",
    "registered", "registering", "located", "placed", "ordered", "ordering",
    "named", "called", "belonging", "belongs", "lived", "living", "working", "works", "worked",
    "delete", "deleting", "deleted", "drop", "dropping", "dropped",
    "insert", "inserting", "inserted", "update", "updating", "updated",
    "add", "adding", "added", "contain", "contains", "containing", "include", "includes",
    "their", "theirs", "his", "her", "hers", "its", "our", "ours", "my", "your", "yours",
    "them", "they", "it", "we", "us", "you", "he", "she", "him", "i"
}

# Common domain nouns that typically denote columns rather than tables
KNOWN_COLUMN_WORDS = {
    "salary", "salaries", "wage", "wages", "pay", "price", "cost", "amount",
    "fee", "rate", "grade", "marks", "percent", "percentage",
    "age", "dob", "birthdate", "birthday", "date", "year", "month", "day",
    "email", "mail", "phone", "mobile", "contact", "address", "city",
    "state", "country", "zip", "postal", "status", "type", "description"
}


@dataclass
class SchemaAvailabilityResult:
    """Result of schema availability analysis."""

    is_available: bool
    relevant_tables: list[TableSchema] = field(default_factory=list)
    missing_tables: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    missing_info: MissingSchemaInfo | None = None
    clarification_message: str | None = None


class SchemaAvailabilityService:
    """Evaluates question requirements against active schema and detects missing elements."""

    def analyze(
        self,
        question: str,
        active_schema: DatabaseSchema,
    ) -> SchemaAvailabilityResult:
        """Analyze if the schema needed for the question exists in the active schema.

        Args:
            question: The natural-language query.
            active_schema: The combined database schema (live tables + user-provided tables).

        Returns:
            SchemaAvailabilityResult indicating availability, relevant tables, or missing elements.
        """
        active_tables_map: dict[str, TableSchema] = {
            t.name.lower(): t for t in active_schema.tables
        }

        # Build lookup of columns across all active tables
        # col_name_lower -> list of table_names that have this column
        column_to_tables: dict[str, list[str]] = {}
        for t in active_schema.tables:
            for col in t.columns:
                column_to_tables.setdefault(col.name.lower(), []).append(t.name)

        # Extract words/tokens from question
        words = self._extract_tokens(question)

        relevant_table_names: set[str] = set()
        missing_tables: set[str] = set()
        missing_columns: set[str] = set()

        for word in words:
            singular = self._singularize(word)
            plural = self._pluralize(word)

            # 1. Check if word matches an active table
            matched_table = self._find_matching_table(word, singular, plural, active_tables_map)
            if matched_table:
                relevant_table_names.add(matched_table)
                continue

            # 2. Check if word matches an active column
            matched_col_tables = self._find_matching_column(word, singular, plural, column_to_tables)
            if matched_col_tables:
                for t_name in matched_col_tables:
                    relevant_table_names.add(t_name)
                continue

            # 3. If word is not in active schema: determine if it is a missing table or column
            if word in KNOWN_COLUMN_WORDS or singular in KNOWN_COLUMN_WORDS:
                missing_columns.add(word)
            else:
                # Potential entity/table name (pluralized by default for table conventions)
                entity_table_name = plural if not word.endswith("s") else word
                # If question specifically mentions "employee" or "order" etc.
                missing_tables.add(entity_table_name)

        # If any tables were matched as relevant, resolve their TableSchema objects
        relevant_tables: list[TableSchema] = [
            active_tables_map[t_name.lower()]
            for t_name in sorted(relevant_table_names)
            if t_name.lower() in active_tables_map
        ]

        # If missing tables were detected:
        if missing_tables:
            sorted_missing_tables = sorted(missing_tables)
            sorted_missing_cols = sorted(missing_columns)
            primary_missing = sorted_missing_tables[0]

            msg = (
                f"The '{primary_missing}' table is not available in the connected database.\n\n"
                f"Please provide the schema for the {primary_missing} table, including:\n"
                f"- column names\n"
                f"- data types\n"
                f"- primary key\n"
                f"- foreign keys/relationships if applicable."
            )

            missing_info = MissingSchemaInfo(
                missing_tables=sorted_missing_tables,
                missing_columns=sorted_missing_cols,
                available_tables=sorted(list(active_tables_map.keys())),
                message=msg,
            )

            return SchemaAvailabilityResult(
                is_available=False,
                relevant_tables=relevant_tables,
                missing_tables=sorted_missing_tables,
                missing_columns=sorted_missing_cols,
                missing_info=missing_info,
                clarification_message=msg,
            )

        # If missing columns detected (even if no missing tables):
        if missing_columns and not relevant_tables:
            sorted_missing_cols = sorted(missing_columns)
            msg = (
                f"Required column(s) {sorted_missing_cols} are not available in the current database schema.\n\n"
                f"Please provide the table schema containing these column(s)."
            )
            missing_info = MissingSchemaInfo(
                missing_tables=[],
                missing_columns=sorted_missing_cols,
                available_tables=sorted(list(active_tables_map.keys())),
                message=msg,
            )
            return SchemaAvailabilityResult(
                is_available=False,
                relevant_tables=relevant_tables,
                missing_tables=[],
                missing_columns=sorted_missing_cols,
                missing_info=missing_info,
                clarification_message=msg,
            )

        # If no specific tables were matched but active schema has tables,
        # include all active tables as relevant context for the query
        if not relevant_tables and active_schema.tables:
            relevant_tables = list(active_schema.tables)

        return SchemaAvailabilityResult(
            is_available=True,
            relevant_tables=relevant_tables,
            missing_tables=[],
            missing_columns=[],
            missing_info=None,
            clarification_message=None,
        )

    def _extract_tokens(self, text: str) -> list[str]:
        """Extract candidate domain words from text, ignoring stopwords and punctuation."""
        raw_words = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", text.lower())
        tokens = []
        for w in raw_words:
            if len(w) > 2 and w not in STOPWORDS:
                tokens.append(w)
        return tokens

    def _find_matching_table(
        self,
        word: str,
        singular: str,
        plural: str,
        tables_map: dict[str, TableSchema],
    ) -> str | None:
        """Find if word, singular, or plural matches a table in tables_map."""
        for candidate in (word, singular, plural):
            if candidate in tables_map:
                return tables_map[candidate].name
        return None

    def _find_matching_column(
        self,
        word: str,
        singular: str,
        plural: str,
        column_to_tables: dict[str, list[str]],
    ) -> list[str]:
        """Find if word, singular, or plural matches any column in column_to_tables."""
        for candidate in (word, singular, plural):
            if candidate in column_to_tables:
                return column_to_tables[candidate]
        return []

    @staticmethod
    def _singularize(word: str) -> str:
        """Simple English singularization for schema matching."""
        if word.endswith("ies") and len(word) > 3:
            return word[:-3] + "y"
        if word.endswith("ses") and len(word) > 3:
            return word[:-2]
        if word.endswith("s") and not word.endswith("ss") and len(word) > 2:
            return word[:-1]
        return word

    @staticmethod
    def _pluralize(word: str) -> str:
        """Simple English pluralization for schema matching."""
        if word.endswith("y") and not word.endswith(("ay", "ey", "oy", "uy")):
            return word[:-1] + "ies"
        if word.endswith(("s", "sh", "ch", "x", "z")):
            return word + "es"
        return word + "s"
