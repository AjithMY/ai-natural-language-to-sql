# AI-Based Natural Language to SQL Query Generation, Optimization and Performance Analysis System

[![Test Suite](https://img.shields.io/badge/tests-534%20passed%20%7C%200%20failed-success)](tests/)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.14-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688)](https://fastapi.tiangolo.com/)
[![MySQL](https://img.shields.io/badge/MySQL-5.7%2B%20%7C%208.0%2B-4479A1)](https://www.mysql.com/)
[![SQLGlot](https://img.shields.io/badge/SQLGlot-AST%20Validator-orange)](https://github.com/tobymao/sqlglot)
[![Google GenAI](https://img.shields.io/badge/Google%20GenAI-gemini--3.6--flash-8E24AA)](https://ai.google.dev/)

An enterprise-grade, secure, and production-ready natural language database interface. Converts human questions into validated MySQL `SELECT` queries, analyzes query plans, proposes non-destructive AST-level optimizations, and benchmarks execution performance via high-resolution timers—presented in an interactive dark-mode dashboard.

---

## Table of Contents

- [Overview & Purpose](#overview--purpose)
- [Key Features](#key-features)
- [Technology Stack](#technology-stack)
- [System Architecture](#system-architecture)
- [Complete Phase History](#complete-phase-history)
- [Database & Schema Structure](#database--schema-structure)
- [Gemini Integration](#gemini-integration)
- [Core Workflows](#core-workflows)
  - [1. Natural Language to SQL Workflow](#1-natural-language-to-sql-workflow)
  - [2. Schema Clarification & Temporary Schemas](#2-schema-clarification--temporary-schemas)
  - [3. SQL Validation & AST Safety Model](#3-sql-validation--ast-safety-model)
  - [4. Safe Query Execution](#4-safe-query-execution)
  - [5. MySQL EXPLAIN Query Plan Analysis](#5-mysql-explain-query-plan-analysis)
  - [6. Query Optimization](#6-query-optimization)
  - [7. Performance Benchmarking](#7-performance-benchmarking)
- [Frontend Dashboard](#frontend-dashboard)
- [API Reference](#api-reference)
- [Environment Configuration](#environment-configuration)
- [Installation & Setup](#installation--setup)
- [Running the Application](#running-the-application)
- [Testing & Quality Assurance](#testing--quality-assurance)
- [Demonstration Guide](#demonstration-guide)
- [Security Model & Safeguards](#security-model--safeguards)
- [Project Boundaries & Limitations](#project-boundaries--limitations)

---

## Overview & Purpose

Non-technical users often require analytical insights from relational databases but lack mastery of SQL syntax, relational algebra, and performance indexing. Traditional generative AI integrations carry significant risks: hallucinated tables, SQL injection, accidental database modification (`DROP`, `DELETE`), or unindexed full table scans that exhaust server resources.

This system bridges that gap by providing:
1. **Accurate Generation**: Schema-aware prompt engineering powered by Google Gemini.
2. **Deterministic Safety**: An Abstract Syntax Tree (AST) validation layer (Phase 4A) rejecting all non-read-only queries before execution.
3. **Execution Guardrails**: Statement-level timeouts and result set truncation (Phase 4B).
4. **Performance Transparency**: MySQL `EXPLAIN` query plan analysis (Phase 4C) and statistical benchmarking (Phase 4E).
5. **AST Optimization**: Rule-based SQL transformations and index recommendations (Phase 4D).
6. **Unified User Interface**: A modern web dashboard orchestrating independent user actions (Phase 5A).

---

## Key Features

- **Schema Availability Detection**: Detects missing tables/columns before calling the LLM and initiates structured user clarification.
- **Temporary User Schemas**: In-memory schema definitions allowing users to generate queries for pending tables without modifying the MySQL catalog.
- **Strict Read-Only Guarantee**: AST parsing blocks `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `REPLACE`, `GRANT`, `REVOKE`, `SET`, `CALL`, and multi-statement queries.
- **Metadata Isolation**: Schema inspection strictly returns schema definitions (columns, types, keys); no table row data is ever exposed during schema inspection.
- **Independent Operations**: SQL Generation strictly displays the SQL and stops. Execution, EXPLAIN, optimization, and performance benchmarking happen **only** when explicitly triggered by the user.
- **High-Resolution Performance Profiling**: Wall-clock query latency measurement via `time.perf_counter()`, discarding warmup runs and computing average, median, min, and max in milliseconds.
- **Credential Protection**: Database passwords, connection strings, and Gemini API keys are redacted from logs, API error payloads, and frontend alerts.

---

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Web Framework** | FastAPI (Python 3.10+) | Asynchronous, OpenAPI-compliant REST API |
| **Server** | Uvicorn | ASGI production server |
| **ORM / Database Driver** | SQLAlchemy 2.0 + PyMySQL | Connection pooling, schema reflection, and transactional safety |
| **Relational Database** | MySQL 5.7+ / 8.0+ | Underlying persistent data store (`minor_project`) |
| **AI / LLM** | Google GenAI SDK (`google-genai`) | Natural language to SQL translation using `gemini-3.6-flash` |
| **AST Parser & Validator** | SQLGlot | Statement inspection, dialect parsing, syntax trees, optimization |
| **Data Validation** | Pydantic v2 + Pydantic-Settings | Request/response model validation and environment variable loading |
| **Frontend UI** | HTML5, Vanilla CSS3, JavaScript (ES6+) | Modern dark-mode dashboard served statically via FastAPI |
| **Testing** | Pytest, AnyIO, Starlette TestClient | Unit, regression, and end-to-end integration test suites |

---

## System Architecture

```
User (Browser)
     │
     ▼
Frontend Dashboard  (http://localhost:8000/)
     │
     ▼
FastAPI Application  (app/main.py)
 ├── RequestIDMiddleware    (attaches X-Request-ID to logs & responses)
 ├── CORSMiddleware         (allowed origins validation)
 └── StaticFiles Mount      (/frontend/index.html, styles.css, app.js)
     │
     ▼
API v1 Router  (app/api/v1/)
     ├── POST /query/generate ──────► QueryGenerationService
     │                                 ├── SchemaService (MySQL Live Schema)
     │                                 ├── SchemaAvailabilityService
     │                                 ├── PromptBuilder
     │                                 ├── GeminiService (Gemini API)
     │                                 └── SQLValidatorService (SQLGlot AST)
     │
     ▼ (User reviews SQL and explicitly triggers an independent action)
     │
     ├── POST /query/execute ──────► SQLExecutionService (Timeout + Max Rows) ──► MySQL
     ├── POST /query/explain ──────► QueryPlanService (EXPLAIN <sql>) ──────────► MySQL
     ├── POST /query/optimize ─────► QueryOptimizationService (AST Rewriter)
     └── POST /query/performance ──► QueryPerformanceService (perf_counter) ────► MySQL
```

---

## Complete Phase History

| Phase | Milestone | Description | Test Count | Status |
|---|---|---|---|---|
| **Phase 1** | Database & Schema | Connection pooling, schema inspection via SQLAlchemy, legacy endpoints | 20 | ✅ Complete |
| **Phase 2** | Backend Foundation | Versioned `/api/v1/` routes, Pydantic settings, `X-Request-ID` middleware, CORS | 37 | ✅ Complete |
| **Phase 3** | Gemini NL → SQL | Prompt engineering, Google GenAI integration, non-executing query generation | 49 | ✅ Complete |
| **Phase 4A** | SQL Validation & Safety | SQLGlot AST validation, read-only SELECT enforcement, anti-injection | 62 | ✅ Complete |
| **Phase 4B** | Safe SQL Execution | Controlled execution, statement timeouts, result set truncation (max 1000) | 58 | ✅ Complete |
| **Phase 4C** | Query Plan Analysis | MySQL `EXPLAIN` analysis without executing the underlying query | 71 | ✅ Complete |
| **Phase 4D** | Query Optimization | AST rule-based query rewrite, index observations, side-by-side comparison | 114 | ✅ Complete |
| **Clarification** | Schema Clarification | Missing schema detection, temporary schema parsing, source attribution | 21 | ✅ Complete |
| **Phase 4E** | Performance Analysis | Multi-run benchmarking via `time.perf_counter()`, warmup exclusion, metrics | 65 | ✅ Complete |
| **Phase 5A** | Frontend Integration | Unified dashboard, copy action, explain labeling, error sanitization | 16 | ✅ Complete |
| **Phase 5B** | System Audit | Verification of all 534 tests, database consistency, and demo scenarios | 534 | ✅ Complete |
| **Phase 5C** | Documentation & Package | Comprehensive README and submission documentation | 534 | ✅ Complete |

---

## Database & Schema Structure

The system connects to a MySQL database named **`minor_project`**. The schema simulates a university academic system with foreign keys and cascade rules:

```
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│   departments   │       │    students     │       │     courses     │
├─────────────────┤       ├─────────────────┤       ├─────────────────┤
│ id (PK)         │◄──────│ id (PK)         │       │ id (PK)         │
│ name (UNIQUE)   │       │ name            │       │ course_name     │
│ created_at      │       │ email (UNIQUE)  │       │ credits         │
└─────────────────┘       │ marks           │       │ department_id   │──┐
                          │ department_id   │──┐    │ created_at      │  │
                          │ created_at      │  │    └─────────────────┘  │
                          └─────────────────┘  │                         │
                                               │    ┌─────────────────┐  │
                                               │    │   enrollments   │  │
                                               │    ├─────────────────┤  │
                                               └───►│ id (PK)         │  │
                                                    │ student_id (FK) │  │
                                                    │ course_id (FK)  │◄─┘
                                                    │ enrolled_at     │
                                                    │ UNIQUE(stud,crs)│
                                                    └─────────────────┘
```

### Table Definitions

1. **`departments`**: Academic departments.
   - `id`: `INT AUTO_INCREMENT PRIMARY KEY`
   - `name`: `VARCHAR(100) NOT NULL UNIQUE`
   - `created_at`: `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
2. **`students`**: Registered students.
   - `id`: `INT AUTO_INCREMENT PRIMARY KEY`
   - `name`: `VARCHAR(100) NOT NULL`
   - `email`: `VARCHAR(150) UNIQUE`
   - `marks`: `INT DEFAULT NULL`
   - `department_id`: `INT`, `FOREIGN KEY` referencing `departments(id)` ON DELETE SET NULL ON UPDATE CASCADE
   - `created_at`: `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
3. **`courses`**: Course catalog.
   - `id`: `INT AUTO_INCREMENT PRIMARY KEY`
   - `course_name`: `VARCHAR(150) NOT NULL`
   - `credits`: `INT NOT NULL DEFAULT 3`
   - `department_id`: `INT`, `FOREIGN KEY` referencing `departments(id)` ON DELETE SET NULL ON UPDATE CASCADE
   - `created_at`: `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
4. **`enrollments`**: Student-course enrollment junction.
   - `id`: `INT AUTO_INCREMENT PRIMARY KEY`
   - `student_id`: `INT NOT NULL`, `FOREIGN KEY` referencing `students(id)` ON DELETE CASCADE
   - `course_id`: `INT NOT NULL`, `FOREIGN KEY` referencing `courses(id)` ON DELETE CASCADE
   - `enrolled_at`: `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
   - `UNIQUE KEY uq_student_course (student_id, course_id)`

---

## Gemini Integration

- **SDK**: Official Google GenAI SDK (`google-genai`).
- **Model**: Configured in `app/core/config.py` as **`gemini-3.6-flash`** (configurable via `GEMINI_MODEL` in `.env`).
- **Prompt Engineering (`app/services/prompt_builder.py`)**:
  - Injects live table schemas, column names, data types, and foreign key relationships.
  - Enforces strict constraints: generate **only** standard MySQL `SELECT` statements; never output markdown formatting or explanatory text; reference only verified tables and columns.
- **Output Sanitization (`app/services/gemini_service.py`)**:
  - Automatically strips markdown code fences (````sql ... ````).
  - Translates API errors to safe `GeminiServiceError` exceptions without exposing API keys.

---

## Core Workflows

### 1. Natural Language to SQL Workflow

```
User enters question: "Show top 5 students with highest marks"
                   ↓
Schema availability check determines relevant tables (students)
                   ↓
Gemini generates SQL: SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;
                   ↓
Phase 4A AST validator confirms query is read-only and safe
                   ↓
Frontend displays: Question, Relevant Schema Cards, Generated SQL, Validation Badge
                   ↓
STOP. Query is NOT executed automatically.
```

### 2. Schema Clarification & Temporary Schemas

When a user asks about a table that does not exist in the database (e.g., *"Show all employee salaries"*):
1. **Detection**: `SchemaAvailabilityService` detects that `employees` is not present in the live MySQL catalog.
2. **Clarification**: Returns HTTP 200 with `status="SCHEMA_REQUIRED"`, listing the missing tables and presenting a clarification prompt.
3. **User Input**: The user provides temporary schema definitions in plain text (e.g., `employees: id INT, name VARCHAR(100), salary DECIMAL(10,2)`).
4. **In-Memory Combining**: The temporary schema is parsed and combined with the live schema in memory only.
5. **Safe Generation**: SQL is generated using the temporary schema and badged with `source="user_provided"`.
6. **Database Integrity**: The missing table is **never created in MySQL**, and no synthetic data is inserted. If the user subsequently clicks *Execute Query*, the backend returns a real database table-not-found error safely.

### 3. SQL Validation & AST Safety Model

Implemented in `app/services/sql_validator.py` using **SQLGlot**:
- **Dialect**: MySQL.
- **Statement Enforcement**: Queries must parse into a single `exp.Select` statement.
- **Prohibited AST Nodes**: Rejects `Insert`, `Update`, `Delete`, `Drop`, `AlterTable`, `Create`, `TruncateTable`, etc.
- **Multi-statement Detection**: Rejects semicolons followed by secondary commands.
- **Schema Grounding**: Rejects queries referencing tables or columns absent from the active schema.

### 4. Safe Query Execution

Implemented in `app/services/sql_execution_service.py`:
- Validates SQL via Phase 4A before execution.
- Injects a statement-level timeout hint: `SELECT /*+ MAX_EXECUTION_TIME(30000) */ ...`.
- Clamps returned rows to `SQL_MAX_ROWS` (default: 1000) to avoid client memory exhaustion.
- Formats result sets into JSON arrays with preserved `NULL` values.

### 5. MySQL EXPLAIN Query Plan Analysis

Implemented in `app/services/query_plan_service.py`:
- Validates query through Phase 4A.
- Prefixes query with `EXPLAIN` and executes `EXPLAIN <sql>` against MySQL.
- **Does not execute the underlying SELECT query.**
- Parses raw EXPLAIN rows into structured models (`select_type`, `table`, `type`, `possible_keys`, `key`, `rows`, `extra`).
- Generates human-readable analysis observations (identifying full table scans `ALL`, file sorts, temporary tables, and index usages).

### 6. Query Optimization

Implemented in `app/services/query_optimization_service.py`:
- Parses SQL into an AST.
- Applies deterministic, safe optimization rules:
  - **`select_star_expansion`**: Replaces `SELECT *` with explicit column lists from schema metadata.
  - **`redundant_limit`**: Normalizes duplicate or non-constraining `LIMIT` clauses.
  - **`index_recommendations`**: Analyzes `WHERE` and `ORDER BY` columns to recommend compound or secondary indexes.
- Validates the optimized SQL before returning.
- **Does not automatically overwrite or execute the query.** The user receives both original and candidate SQL side-by-side.

### 7. Performance Benchmarking

Implemented in `app/services/query_performance_service.py`:
- Validates query through Phase 4A.
- Performs **1 warmup run** (discarded and excluded from reported statistics) to populate MySQL buffer pools.
- Executes the query `run_count` times (1–20, default 5) using Python's high-resolution `time.perf_counter()`.
- Fetches all rows to ensure complete database round-trip processing.
- Returns timing array (`timings_ms`) and calculated statistics:
  $$\text{Average} = \frac{1}{N}\sum t_i, \quad \text{Median}, \quad \text{Min}, \quad \text{Max}$$
- Uses milliseconds (`ms`) with 3 decimal precision. Never fabricates synthetic timing values.

---

## Frontend Dashboard

The single-page dashboard is served at `http://localhost:8000/frontend/index.html` (with automatic redirect from `/`):

- **Input Section**: Natural-language prompt textarea with character counter, example prompt chips, and `Generate SQL` button.
- **Clarification Panel**: Automatically expands if `SCHEMA_REQUIRED` is returned, allowing temporary schema submission.
- **Schema Cards**: Cards showing table metadata, PK badges, and foreign-key relationship pills (`FK → target_table.column`).
- **Generated SQL Card**: Syntax-highlighted code block, validation badge (`✓ Valid and Safe`), and dedicated `[📋 Copy SQL]` button.
- **Independent Action Bar**:
  - `[▶ Execute Query]`
  - `[📊 Explain Query]`
  - `[⚙ Optimize Query]`
  - `[📈 Analyze Performance]`
- **Tabbed Results Container**:
  - **Query Results Tab**: Data table with row counts and column headers.
  - **Query Plan Tab**: Tabular EXPLAIN output with highlighted `ALL` table scans and textual analysis observations.
  - **Optimization Tab**: Side-by-side SQL diff and list of applied optimization rules.
  - **Performance Tab**: Metric tiles (Runs, Average, Median, Min, Max) and individual run bar charts.

---

## API Reference

All primary API routes are versioned under `/api/v1/`.

### 1. Health & Schema Routes

#### `GET /api/v1/health`
Checks server health and database connectivity.
- **Response (200)**:
  ```json
  {
    "status": "healthy",
    "database": "connected",
    "request_id": "550e8400-e29b-41d4-a716-446655440000"
  }
  ```

#### `GET /api/v1/schema`
Retrieves live database schema metadata (no table row data).
- **Response (200)**:
  ```json
  {
    "database": "minor_project",
    "tables": [
      {
        "name": "students",
        "columns": [
          {"name": "id", "type": "INTEGER", "nullable": false, "autoincrement": true},
          {"name": "name", "type": "VARCHAR(100)", "nullable": false},
          {"name": "marks", "type": "INTEGER", "nullable": true}
        ],
        "primary_keys": ["id"],
        "foreign_keys": [],
        "source": "live"
      }
    ],
    "table_count": 4,
    "request_id": "..."
  }
  ```

---

### 2. Query Endpoints

#### `POST /api/v1/query/generate`
Translates a question into SQL. **Does NOT execute the query.**
- **Request Body**:
  ```json
  {
    "question": "Show the top 5 students with the highest marks",
    "user_schema_text": null
  }
  ```
- **Response (200 - Success)**:
  ```json
  {
    "status": "GENERATED",
    "question": "Show the top 5 students with the highest marks",
    "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;",
    "model": "gemini-3.6-flash",
    "validation": {
      "valid": true,
      "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;",
      "errors": [],
      "warnings": []
    },
    "relevant_schemas": [...],
    "schema_source": "live",
    "request_id": "..."
  }
  ```
- **Response (200 - Clarification Required)**:
  ```json
  {
    "status": "SCHEMA_REQUIRED",
    "question": "Show all employees and their salaries",
    "sql": "",
    "missing_schema": {
      "missing_tables": ["employees"],
      "missing_columns": [],
      "available_tables": ["courses", "departments", "enrollments", "students"]
    },
    "message": "Required table 'employees' is not present in database.",
    "schema_source": "live"
  }
  ```

#### `POST /api/v1/query/execute`
Safely executes a validated `SELECT` query against MySQL.
- **Request Body**:
  ```json
  {
    "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 2;"
  }
  ```
- **Response (200)**:
  ```json
  {
    "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 2;",
    "columns": ["name", "marks"],
    "rows": [
      ["Charlie Brown", 91],
      ["Alice Johnson", 85]
    ],
    "row_count": 2,
    "truncated": false,
    "max_rows": 1000,
    "validation": {"valid": true, "errors": []},
    "request_id": "..."
  }
  ```
- **Error (422)**: Returned if SQL fails safety validation.

#### `POST /api/v1/query/explain`
Obtains the MySQL `EXPLAIN` query plan. **Does NOT execute the SELECT.**
- **Request Body**:
  ```json
  {
    "sql": "SELECT name, marks FROM students ORDER BY marks DESC;"
  }
  ```
- **Response (200)**:
  ```json
  {
    "sql": "SELECT name, marks FROM students ORDER BY marks DESC;",
    "plan": [
      {
        "id": 1,
        "select_type": "SIMPLE",
        "table": "students",
        "type": "ALL",
        "possible_keys": null,
        "key": null,
        "rows": 3,
        "filtered": 100.0,
        "extra": "Using filesort"
      }
    ],
    "analysis": [
      "Full table scan on 'students'",
      "Using filesort"
    ],
    "validation": {"valid": true, "errors": []},
    "request_id": "..."
  }
  ```

#### `POST /api/v1/query/optimize`
Analyzes query AST and produces optimization suggestions without modifying the original query.
- **Request Body**:
  ```json
  {
    "sql": "SELECT * FROM students;"
  }
  ```
- **Response (200)**:
  ```json
  {
    "original_sql": "SELECT * FROM students;",
    "optimized_sql": "SELECT id, name, email, marks, department_id, created_at FROM students;",
    "changed": true,
    "optimizations": [
      {
        "rule": "select_star_expansion",
        "description": "Expanded wildcard '*' to explicit table columns.",
        "original_fragment": "*",
        "optimized_fragment": "id, name, email, marks, department_id, created_at"
      }
    ],
    "index_recommendations": [],
    "validation": {"valid": true, "errors": []},
    "optimized_validation": {"valid": true, "errors": []},
    "request_id": "..."
  }
  ```

#### `POST /api/v1/query/performance`
Benchmarks query latency across repeated executions.
- **Request Body**:
  ```json
  {
    "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;",
    "run_count": 5
  }
  ```
- **Response (200)**:
  ```json
  {
    "sql": "SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;",
    "run_count": 5,
    "warmup_runs": 1,
    "timings_ms": [0.872, 0.551, 0.504, 0.549, 0.501],
    "average_ms": 0.595,
    "median_ms": 0.549,
    "min_ms": 0.501,
    "max_ms": 0.872,
    "validation": {"valid": true, "errors": []},
    "request_id": "..."
  }
  ```

---

## Environment Configuration

Create a `.env` file in the project root based on `.env.example`:

```ini
# Database Connection
DB_HOST=localhost
DB_PORT=3306
DB_NAME=minor_project
DB_USER=root
DB_PASSWORD=your_mysql_password

# Application Settings
CORS_ORIGINS=http://localhost:3000,http://localhost:5173,http://localhost:8000
ENVIRONMENT=development
APP_DEBUG=False

# Google Gemini AI
GEMINI_API_KEY=your_actual_gemini_api_key_here
GEMINI_MODEL=gemini-3.6-flash

# Execution Guardrails
SQL_MAX_ROWS=1000
SQL_QUERY_TIMEOUT_SECONDS=30
```

### Configuration Parameters

| Variable | Default | Description |
|---|---|---|
| `DB_HOST` | `localhost` | MySQL hostname or IP address |
| `DB_PORT` | `3306` | MySQL port |
| `DB_NAME` | `minor_project` | Database name |
| `DB_USER` | `root` | Database username |
| `DB_PASSWORD` | *(empty)* | Database user password |
| `CORS_ORIGINS` | `http://localhost:8000` | Allowed origins for browser requests |
| `GEMINI_API_KEY` | *(required)* | Google Gemini API key from AI Studio |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Gemini model name |
| `SQL_MAX_ROWS` | `1000` | Maximum rows returned per query execution |
| `SQL_QUERY_TIMEOUT_SECONDS` | `30` | Per-statement execution timeout in seconds |

---

## Installation & Setup

### 1. Prerequisites
- Python 3.10, 3.11, 3.12, or 3.14.
- MySQL 5.7+ or 8.0+ running locally or remotely.
- A free Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey).

### 2. Clone & Setup Virtual Environment

```bash
cd "Minor project"

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Setup MySQL Database

Log in to MySQL and initialize the schema and sample records:

```bash
mysql -u root -p < setup_database.sql
```

This creates the database `minor_project` with tables `departments`, `students`, `courses`, `enrollments`, and sample seed data.

---

## Running the Application

Start the FastAPI application with Uvicorn:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Access the system:
- **Interactive Dashboard**: [http://localhost:8000/](http://localhost:8000/) (redirects to `/frontend/index.html`)
- **Interactive Swagger API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc Technical Docs**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## Testing & Quality Assurance

The project includes comprehensive test suites covering all modules:

```bash
pytest -q
```

### Test Suite Structure

| Test File | Focus | Test Count |
|---|---|---|
| `test_connection.py` | Database connectivity & health checks | 6 |
| `test_schema.py` | Schema reflection & Pydantic models | 14 |
| `test_phase2.py` | Middleware, CORS, Request ID, V1 routing | 37 |
| `test_phase3.py` | Prompt engineering, Gemini service, NL-to-SQL | 49 |
| `test_phase4.py` | SQLGlot AST safety validation & anti-injection | 62 |
| `test_phase4b.py` | Safe execution, timeouts, row limits | 58 |
| `test_phase4c.py` | MySQL EXPLAIN plan analysis & observations | 71 |
| `test_phase4d.py` | Query optimization, AST rewriting rules | 114 |
| `test_schema_clarification.py` | Missing schema detection, temporary schemas | 21 |
| `test_phase4e.py` | Performance benchmarking & metrics | 65 |
| `test_phase5a.py` | Frontend integration, asset delivery, API flows | 16 |
| **Total Passing Tests** | | **534 / 534 (100%)** |

---

## Demonstration Guide

### Demo Scenario 1: Standard Query Flow
1. Open `http://localhost:8000/`.
2. Enter prompt: `Show the top 5 students with the highest marks`
3. Click `Generate SQL`.
4. Observe:
   - Schema card for `students` displays columns and types with badge `Live Database`.
   - Generated SQL is displayed: `SELECT name, marks FROM students ORDER BY marks DESC LIMIT 5;`.
   - Validation badge confirms `✓ Valid and Safe`.
   - **Query does not execute automatically.**
5. Click `Execute Query`: View the student names and marks in the Results tab.
6. Click `Explain Query`: View the EXPLAIN plan table and observations in the Query Plan tab.
7. Click `Optimize Query`: View AST rule evaluations and index recommendations.
8. Click `Analyze Performance`: View 5 execution runs with high-resolution latencies and statistics.

### Demo Scenario 2: Relational Join Query
1. Enter prompt: `Show students with their department names`
2. Click `Generate SQL`.
3. Observe schema cards for both `students` and `departments`, including the foreign key indicator `FK → departments.id`.
4. Gemini outputs an inner join:
   ```sql
   SELECT students.name, departments.name AS department_name
   FROM students
   JOIN departments ON students.department_id = departments.id;
   ```
5. Click `Execute Query` to verify relationship resolution.

### Demo Scenario 3: Missing Schema Clarification
1. Enter prompt: `Show all employees and their salaries`
2. Click `Generate SQL`.
3. Notice that `employees` does not exist in `minor_project`.
4. The system presents the **Schema Required** panel with a template.
5. In the input box, provide:
   ```text
   employees
   - id INT PRIMARY KEY
   - name VARCHAR(100)
   - salary DECIMAL(10,2)
   ```
6. Click `Continue SQL Generation`.
7. SQL is generated: `SELECT name, salary FROM employees;` with source badge `User-Provided Temporary Schema`.
8. Click `Execute Query`: MySQL safely returns a table-not-found error without crashing or creating any MySQL tables.

### Demo Scenario 4: Security & Injection Block
1. Enter an unsafe query into an API client or test suite: `DROP TABLE students;` or `SELECT * FROM students; DELETE FROM students;`.
2. Notice the response: HTTP 422 `SQL_VALIDATION_FAILED` with errors explaining that non-SELECT or multi-statement commands are rejected before database execution.

---

## Security Model & Safeguards

- **Read-Only Enforcement**: Pure AST validation rejects all modification statements.
- **SQL Injection Prevention**: Rejects comments syntax (`--`, `/* */`), stacked queries, and unrecognized identifiers.
- **Credential Masking**: All database connection URLs and API keys are isolated in `.env`. Error handlers scrub passwords and tokens using regex replacements.
- **Resource Protection**: Statement timeouts terminate runaway queries, and row limits prevent memory exhaustion.
- **OpenAPI Route Isolation**: Internal frontend routes and static mount points are excluded from OpenAPI documentation to prevent route probing.

---

## Project Boundaries & Limitations

- **SELECT Queries Only**: By design, data insertion, deletion, and DDL schema modifications are disallowed.
- **Benchmark Run Limit**: Performance benchmarking is bounded between 1 and 20 iterations to avoid database saturation.
- **Temporary Schema Scope**: User-supplied temporary schemas exist only in memory during the request and are never persisted to the MySQL catalog.
