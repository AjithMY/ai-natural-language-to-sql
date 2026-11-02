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

# Common English stopwords, question words, verbs, adjectives, prepositions, conjunctions,
# and database/query terminology that do NOT represent domain tables or columns.
STOPWORDS = {
    # Articles, demonstratives, pronouns
    "a", "an", "the", "this", "that", "these", "those", "each", "every", "all",
    "any", "both", "some", "few", "more", "most", "other", "another", "such",
    "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "i", "me", "my", "myself", "we", "us", "our", "ours", "ourselves",
    "you", "your", "yours", "yourself", "yourselves",
    "he", "him", "his", "himself", "she", "her", "hers", "herself",
    "it", "its", "itself", "they", "them", "their", "theirs", "themselves",
    "one", "ones",

    # Prepositions & conjunctions
    "in", "on", "at", "by", "for", "with", "about", "against", "between", "into",
    "through", "during", "before", "after", "above", "below", "to", "from", "up",
    "down", "of", "off", "over", "under", "again", "further", "then", "once",
    "here", "there", "when", "where", "why", "how", "across", "along", "among",
    "behind", "beyond", "onto", "upon", "within", "without", "toward", "towards",
    "via", "throughout", "concerning", "regarding", "including", "excluding",
    "despite", "except", "and", "or", "but", "if", "as", "because", "while",
    "although", "though", "unless", "since", "whether", "either", "neither",

    # Question words & relative pronouns
    "who", "which", "what", "whose", "whom", "whatever", "whichever", "whoever",

    # Auxiliaries & common verbs
    "is", "am", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "do", "does", "did", "doing",
    "can", "could", "will", "would", "shall", "should", "may", "might", "must",

    # Query action verbs & participles
    "show", "shows", "showing", "shown", "list", "lists", "listing", "listed",
    "give", "gives", "giving", "given", "find", "finds", "finding", "found",
    "get", "gets", "getting", "got", "display", "displays", "displaying", "displayed",
    "fetch", "fetches", "fetching", "fetched", "select", "selects", "selecting", "selected",
    "tell", "tells", "telling", "told", "see", "sees", "seeing", "saw", "seen",
    "look", "looks", "looking", "looked", "check", "checks", "checking", "checked",
    "search", "searches", "searching", "searched", "retrieve", "retrieves", "retrieving",
    "return", "returns", "returning", "returned", "provide", "provides", "providing",

    # Common domain verbs / participles often found in database queries
    "enrolled", "enrolls", "enrolling", "enroll", "enrollment",
    "registered", "registers", "registering", "register",
    "assigned", "assigns", "assigning", "assign",
    "offered", "offers", "offering", "offer",
    "taught", "teaches", "teaching", "teach",
    "scored", "scores", "scoring", "score",
    "passed", "passes", "passing", "pass",
    "failed", "fails", "failing", "fail",
    "paid", "pays", "paying", "pay",
    "collected", "collects", "collecting", "collect",
    "held", "holds", "holding", "hold",
    "attended", "attends", "attending", "attend",
    "admitted", "admits", "admitting", "admit",
    "located", "placed", "ordered", "ordering", "named", "called",
    "belonging", "belongs", "belong", "belonged",
    "living", "lived", "lives", "live", "working", "works", "worked", "work",
    "containing", "contains", "contain", "contained",
    "include", "includes", "included",
    "delete", "deleting", "deleted", "drop", "dropping", "dropped",
    "insert", "inserting", "inserted", "update", "updating", "updated",
    "add", "adding", "added",

    # Common adjectives, adverbs & qualifiers
    "top", "bottom", "first", "last", "highest", "higher", "high",
    "lowest", "lower", "low", "greatest", "greater", "great",
    "smallest", "smaller", "small", "maximum", "max", "minimum", "min",
    "total", "average", "avg", "overall", "sum", "count", "number",
    "equal", "equals", "equaled", "equaling",
    "sorted", "sort", "sorting", "descending", "desc", "ascending", "asc",
    "pending", "due", "active", "inactive", "current", "recent",
    "male", "female", "internal", "external",
    "new", "old", "older", "younger",
    "good", "better", "best", "bad", "worse", "worst",
    "nonexistent", "fake", "dummy", "random", "sample", "unknown", "invalid", "test",

    # Database & query meta-terms
    "database", "db", "table", "tables", "column", "columns", "field", "fields",
    "row", "rows", "record", "records", "data", "information", "info",
    "details", "detail", "item", "items", "entry", "entries", "result", "results",
    "summary", "value", "values", "per", "many", "much", "member", "members",
    "payment", "payments", "exam", "exams", "schedule", "schedules",
}


# Common domain nouns that typically denote columns rather than tables
KNOWN_COLUMN_WORDS = {
    "salary", "salaries", "wage", "wages", "pay", "price", "cost", "amount",
    "fee", "rate", "grade", "marks", "percent", "percentage",
    "age", "dob", "birthdate", "birthday", "date", "year", "month", "day",
    "email", "mail", "phone", "mobile", "contact", "address", "city",
    "state", "country", "zip", "postal", "status", "type", "description",
    "credits", "gender", "semester", "cgpa", "building", "designation"
}

# Mapping common phrases/abbreviations to column name stems
COLUMN_PHRASE_MAPPINGS = {
    "room number": "room_no",
    "room no": "room_no",
    "register number": "register_no",
    "registration number": "register_no",
    "register no": "register_no",
    "course name": "course_name",
    "course code": "course_code",
    "course type": "course_type",
    "academic year": "academic_year",
    "total marks": "total_marks",
    "internal marks": "internal_marks",
    "assignment marks": "assignment_marks",
    "external marks": "external_marks",
    "paid amount": "paid_amount",
    "payment status": "payment_status",
    "fee type": "fee_type",
    "due date": "due_date",
    "joining date": "joining_date",
    "start date": "start_date",
    "end date": "end_date",
    "start time": "start_time",
    "attendance percent": "attendance_percent",
    "attendance percentage": "attendance_percent",
    "hod name": "hod_name",
    "admission year": "admission_year",
    "classes held": "classes_held",
    "classes attended": "classes_attended",
    "grade point": "grade_point",
}

# Irregular plural / singular mappings
IRREGULAR_SINGULARS = {
    "faculty": "faculty",
    "faculties": "faculty",
    "people": "person",
    "children": "child",
    "data": "data",
    "datum": "data",
}

IRREGULAR_PLURALS = {
    "faculty": "faculty",
    "person": "people",
    "child": "children",
    "data": "data",
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

        # Build column to table mapping (including normalized forms)
        column_to_tables: dict[str, list[str]] = {}
        for t in active_schema.tables:
            for col in t.columns:
                cname = col.name.lower()
                column_to_tables.setdefault(cname, []).append(t.name)
                # Also index space-separated version if column has underscores
                if "_" in cname:
                    column_to_tables.setdefault(cname.replace("_", " "), []).append(t.name)

        clean_text = self._clean_question(question)
        tokens = self._extract_tokens(clean_text)

        relevant_table_names: set[str] = set()
        matched_tokens: set[int] = set()

        # Phase 1: Compound / Multi-word phrase matching (trigrams and bigrams)
        self._match_compound_phrases(
            tokens,
            active_tables_map,
            column_to_tables,
            relevant_table_names,
            matched_tokens,
        )

        # Phase 2: Single-word matching against tables and columns
        for idx, word in enumerate(tokens):
            if idx in matched_tokens:
                continue

            singular = self._singularize(word)
            plural = self._pluralize(word)

            # 1. Check if word matches an active table
            matched_table = self._find_matching_table(word, singular, plural, active_tables_map)
            if matched_table:
                relevant_table_names.add(matched_table)
                matched_tokens.add(idx)
                continue

            # 2. Check if word matches an active column
            matched_col_tables = self._find_matching_column(word, singular, plural, column_to_tables)
            if matched_col_tables:
                for t_name in matched_col_tables:
                    relevant_table_names.add(t_name)
                matched_tokens.add(idx)
                continue

        # Phase 3: Missing schema detection
        missing_tables: set[str] = set()
        missing_columns: set[str] = set()

        for idx, word in enumerate(tokens):
            if idx in matched_tokens or word in STOPWORDS:
                continue

            if self._is_non_entity(word):
                continue

            singular = self._singularize(word)
            plural = self._pluralize(word)

            if word in KNOWN_COLUMN_WORDS or singular in KNOWN_COLUMN_WORDS:
                # Column check: if not in any active table
                if not self._find_matching_column(word, singular, plural, column_to_tables):
                    missing_columns.add(word)
            else:
                # Entity table check: domain noun not matching any active table
                if not self._find_matching_table(word, singular, plural, active_tables_map):
                    entity_table_name = plural if not word.endswith("s") else word
                    missing_tables.add(entity_table_name)


        # Resolve TableSchema objects for all relevant tables
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

        # If missing columns detected when no tables matched:
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

    def _clean_question(self, text: str) -> str:
        """Normalize question text, replacing dashes and slashes with spaces."""
        text = text.replace("-", " ").replace("/", " ").replace("_", " ")
        return text.lower()

    def _extract_tokens(self, text: str) -> list[str]:
        """Extract candidate words from cleaned text."""
        return re.findall(r"\b[a-zA-Z0-9]+\b", text.lower())

    def _match_compound_phrases(
        self,
        tokens: list[str],
        tables_map: dict[str, TableSchema],
        column_to_tables: dict[str, list[str]],
        relevant_table_names: set[str],
        matched_tokens: set[int],
    ) -> None:
        """Match 2-word and 3-word compounds against table and column names."""
        n = len(tokens)

        # Check 3-word phrases first
        for i in range(n - 2):
            phrase_space = f"{tokens[i]} {tokens[i+1]} {tokens[i+2]}"
            phrase_underscore = f"{tokens[i]}_{tokens[i+1]}_{tokens[i+2]}"

            # Check known phrase mapping
            mapped = COLUMN_PHRASE_MAPPINGS.get(phrase_space)
            if mapped and mapped in column_to_tables:
                for t in column_to_tables[mapped]:
                    relevant_table_names.add(t)
                matched_tokens.update([i, i + 1, i + 2])
                continue

            for cand in (phrase_space, phrase_underscore):
                if cand in column_to_tables:
                    for t in column_to_tables[cand]:
                        relevant_table_names.add(t)
                    matched_tokens.update([i, i + 1, i + 2])
                    break

        # Check 2-word phrases
        for i in range(n - 1):
            if i in matched_tokens and (i + 1) in matched_tokens:
                continue

            phrase_space = f"{tokens[i]} {tokens[i+1]}"
            phrase_underscore = f"{tokens[i]}_{tokens[i+1]}"

            # Check known phrase mapping
            mapped = COLUMN_PHRASE_MAPPINGS.get(phrase_space)
            if mapped:
                if mapped in column_to_tables:
                    for t in column_to_tables[mapped]:
                        relevant_table_names.add(t)
                    matched_tokens.update([i, i + 1])
                    continue
                sing = self._singularize(mapped)
                plur = self._pluralize(mapped)
                t_match = self._find_matching_table(mapped, sing, plur, tables_map)
                if t_match:
                    relevant_table_names.add(t_match)
                    matched_tokens.update([i, i + 1])
                    continue

            # Check compound table matches (e.g., "exam schedules" -> exam_schedules)
            for cand in (phrase_space, phrase_underscore):
                sing = self._singularize(cand)
                plur = self._pluralize(cand)
                t_match = self._find_matching_table(cand, sing, plur, tables_map)
                if t_match:
                    relevant_table_names.add(t_match)
                    matched_tokens.update([i, i + 1])
                    break
                # Also check column match
                col_match = self._find_matching_column(cand, sing, plur, column_to_tables)
                if col_match:
                    for t in col_match:
                        relevant_table_names.add(t)
                    matched_tokens.update([i, i + 1])
                    break

    def _find_matching_table(
        self,
        word: str,
        singular: str,
        plural: str,
        tables_map: dict[str, TableSchema],
    ) -> str | None:
        """Find if word, singular, or plural matches a table in tables_map."""
        for candidate in (word, singular, plural):
            c_clean = candidate.replace(" ", "_")
            if c_clean in tables_map:
                return tables_map[c_clean].name
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
            c_clean = candidate.replace(" ", "_")
            if c_clean in column_to_tables:
                return column_to_tables[c_clean]
            if candidate in column_to_tables:
                return column_to_tables[candidate]
        return []

    def _is_non_entity(self, word: str) -> bool:
        """Check if a word is an adjective, verb form, adverb, or number that is not a table entity."""
        if len(word) <= 2:
            return True
        if word.isdigit():
            return True
        if word in STOPWORDS:
            return True
        # Participles and comparative/superlative suffixes
        if word.endswith(("ed", "ing", "ly", "est", "er", "able", "ible")):
            return True
        return False

    @staticmethod
    def _singularize(word: str) -> str:
        """Reliable English singularization for schema matching."""
        if word in IRREGULAR_SINGULARS:
            return IRREGULAR_SINGULARS[word]
        if word.endswith("ies") and len(word) > 3:
            return word[:-3] + "y"
        if word.endswith("ses") and len(word) > 4:
            return word[:-2]
        if word.endswith("s") and not word.endswith("ss") and len(word) > 2:
            return word[:-1]
        return word

    @staticmethod
    def _pluralize(word: str) -> str:
        """Reliable English pluralization for schema matching."""
        if word in IRREGULAR_PLURALS:
            return IRREGULAR_PLURALS[word]
        if word.endswith("y") and not word.endswith(("ay", "ey", "oy", "uy")):
            return word[:-1] + "ies"
        if word.endswith(("s", "sh", "ch", "x", "z")):
            return word + "es"
        return word + "s"

