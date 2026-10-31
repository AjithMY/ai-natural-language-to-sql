/**
 * QueryMind Dashboard — Frontend Application
 * AI-Powered Natural Language to SQL System
 *
 * Connects to the existing FastAPI backend (Phases 1–4E).
 * All database operations go through the backend API.
 * No SQL is executed directly in the browser.
 * No API keys or credentials are stored in the frontend.
 *
 * API Endpoints used:
 *   GET  /api/v1/health            -- Health check
 *   GET  /api/v1/schema            -- Database schema
 *   POST /api/v1/query/generate    -- NL-to-SQL generation
 *   POST /api/v1/query/execute     -- SQL execution
 *   POST /api/v1/query/explain     -- EXPLAIN plan analysis
 *   POST /api/v1/query/optimize    -- SQL optimization
 *   POST /api/v1/query/performance -- Performance benchmarking (Phase 4E)
 */

// ══════════════════════════════════════════════════════════════
// Configuration
// ══════════════════════════════════════════════════════════════

const API_BASE = 'http://localhost:8000';

// ══════════════════════════════════════════════════════════════
// State
// ══════════════════════════════════════════════════════════════

let currentSQL = '';
let isGenerating = false;
let isExecuting = false;
let isAnalyzing = false;
let isOptimizing = false;
let isPerforming = false;

// ══════════════════════════════════════════════════════════════
// Initialization
// ══════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    checkHealth();
    loadSchema();
    setupInputListeners();
});

function setupInputListeners() {
    const input = document.getElementById('questionInput');
    const charCount = document.getElementById('charCount');

    input.addEventListener('input', () => {
        const len = input.value.length;
        charCount.textContent = `${len} character${len !== 1 ? 's' : ''}`;
    });

    // Ctrl+Enter to generate
    input.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            e.preventDefault();
            generateSQL();
        }
    });
}

// ══════════════════════════════════════════════════════════════
// Health Check
// ══════════════════════════════════════════════════════════════

async function checkHealth() {
    const statusEl = document.getElementById('dbStatus');
    const textEl = document.getElementById('dbStatusText');

    statusEl.className = 'status-indicator checking';
    textEl.textContent = 'Checking…';

    try {
        const res = await fetch(`${API_BASE}/api/v1/health`, {
            signal: AbortSignal.timeout(5000),
        });

        if (res.ok) {
            const data = await res.json();
            if (data.database === 'connected') {
                statusEl.className = 'status-indicator connected';
                textEl.textContent = 'Backend Connected';
            } else {
                statusEl.className = 'status-indicator disconnected';
                textEl.textContent = 'Disconnected';
            }
        } else {
            statusEl.className = 'status-indicator disconnected';
            textEl.textContent = 'Disconnected';
        }
    } catch {
        statusEl.className = 'status-indicator disconnected';
        textEl.textContent = 'Offline';
    }
}

// ══════════════════════════════════════════════════════════════
// Schema Loading
// ══════════════════════════════════════════════════════════════

async function loadSchema() {
    try {
        const res = await fetch(`${API_BASE}/api/v1/schema`, {
            signal: AbortSignal.timeout(5000),
        });

        if (res.ok) {
            const data = await res.json();
            document.getElementById('footerDbName').textContent = data.database || 'minor_project';

            const container = document.getElementById('footerTables');
            container.innerHTML = '';

            const tableCountEl = document.getElementById('tableCount');

            if (data.tables && data.tables.length > 0) {
                if (tableCountEl) {
                    tableCountEl.textContent = data.tables.length;
                }
                data.tables.forEach(t => {
                    const chip = document.createElement('span');
                    chip.className = 'table-chip';
                    chip.textContent = t.name;
                    container.appendChild(chip);
                });
            } else {
                if (tableCountEl) {
                    tableCountEl.textContent = '0';
                }
            }
        }
    } catch {
        document.getElementById('footerDbName').textContent = 'minor_project';
        const tableCountEl = document.getElementById('tableCount');
        if (tableCountEl) tableCountEl.textContent = '—';
    }
}

// ══════════════════════════════════════════════════════════════
// Example Questions
// ══════════════════════════════════════════════════════════════

function setExample(el) {
    const input = document.getElementById('questionInput');
    input.value = el.textContent;
    input.dispatchEvent(new Event('input'));
    input.focus();
}

// ══════════════════════════════════════════════════════════════
// Generate SQL
// ══════════════════════════════════════════════════════════════

async function generateSQL(userSchemaText = null) {
    const input = document.getElementById('questionInput');
    const question = input.value.trim();

    if (!question) {
        showAlert('Please enter a question before generating SQL.', 'warning');
        input.focus();
        return;
    }

    if (isGenerating) return;
    isGenerating = true;

    const btn = document.getElementById('generateBtn');
    setButtonLoading(btn, true, 'Generating…');

    // Reset previous execution / plan results
    resetResults();

    const requestPayload = { question };
    if (userSchemaText) {
        requestPayload.user_schema_text = userSchemaText;
    }

    try {
        const res = await fetch(`${API_BASE}/api/v1/query/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestPayload),
        });

        if (res.ok) {
            const data = await res.json();

            // Check if backend requests schema clarification
            if (data.status === 'SCHEMA_REQUIRED') {
                // Show Missing Schema Panel
                const missingSection = document.getElementById('missingSchemaSection');
                missingSection.classList.remove('hidden');
                document.getElementById('missingSchemaMessage').textContent = data.message || 'Required schema information is missing.';

                // Pre-fill placeholder / template if input is empty
                const schemaInput = document.getElementById('userSchemaInput');
                if (!schemaInput.value && data.missing_schema && data.missing_schema.missing_tables.length > 0) {
                    const tableName = data.missing_schema.missing_tables[0];
                    schemaInput.placeholder = `${tableName}\n- id INT PRIMARY KEY\n- name VARCHAR(100)\n- salary DECIMAL(10,2)\n- department_id INT\nFOREIGN KEY (department_id) -> departments.id`;
                }

                // Render any relevant schemas identified so far
                if (data.relevant_schemas && data.relevant_schemas.length > 0) {
                    renderSchemaCards(data.relevant_schemas);
                    document.getElementById('schemaSection').classList.remove('hidden');
                } else {
                    document.getElementById('schemaSection').classList.add('hidden');
                }

                // Hide generated section until schema is supplied
                document.getElementById('generatedSection').classList.add('hidden');
                document.getElementById('resultsSection').classList.add('hidden');
                hideActionPanel();

                // Hide welcome panel
                hideWelcomePanel();

                showAlert('Missing table/column schema. Please supply schema details above to continue.', 'warning');
                return;
            }

            // Normal generation or generation with temporary schema (status === 'GENERATED')
            currentSQL = data.sql;

            // Hide missing schema section
            document.getElementById('missingSchemaSection').classList.add('hidden');

            // Hide welcome panel
            hideWelcomePanel();

            // Render relevant table schemas (METADATA ONLY, NO ROWS)
            if (data.relevant_schemas && data.relevant_schemas.length > 0) {
                renderSchemaCards(data.relevant_schemas);
                document.getElementById('schemaSection').classList.remove('hidden');
            } else {
                document.getElementById('schemaSection').classList.add('hidden');
            }

            // Show generated SQL section
            document.getElementById('generatedSection').classList.remove('hidden');
            document.getElementById('resultsSection').classList.remove('hidden');
            showActionPanel();

            // Display question
            document.getElementById('questionDisplay').textContent = `"${data.question}"`;

            // Display SQL
            document.getElementById('sqlDisplay').textContent = data.sql;

            // Show temporary schema banner if applicable
            const banner = document.getElementById('schemaSourceBanner');
            if (data.schema_source === 'user_provided' || data.schema_source === 'combined') {
                banner.classList.remove('hidden');
                banner.innerHTML = `<span>ℹ️</span> <span>Generated SQL based on <strong>user-provided schema</strong> — Generated only, not executed automatically. Click <em>Execute Query</em> below to run against MySQL.</span>`;
            } else {
                banner.classList.add('hidden');
            }

            // Validation badge
            const badge = document.getElementById('validationBadge');
            if (data.validation && data.validation.valid) {
                badge.className = 'validation-badge valid';
                badge.textContent = '✓ Valid and Safe';
            } else {
                badge.className = 'validation-badge invalid';
                badge.textContent = '✗ Validation Failed';
            }

            // Model info
            document.getElementById('modelInfo').textContent = `Model: ${data.model || 'unknown'}`;

            // Enable action buttons (including Execute button for explicit user action)
            document.getElementById('executeBtn').disabled = false;
            document.getElementById('analyzeBtn').disabled = false;
            document.getElementById('optimizeBtn').disabled = false;
            document.getElementById('performanceBtn').disabled = false;

        } else {
            const errData = await parseErrorResponse(res);
            showAlert(errData.message, 'error');
        }

    } catch (err) {
        showAlert(
            'Failed to connect to the backend. Ensure the FastAPI server is running on ' + API_BASE,
            'error'
        );
    } finally {
        isGenerating = false;
        setButtonLoading(btn, false, 'Generate SQL', 'bolt');
    }
}

async function generateSQLWithSchema() {
    const schemaInput = document.getElementById('userSchemaInput');
    const schemaText = schemaInput.value.trim();

    if (!schemaText) {
        showAlert('Please enter the missing table schema before continuing.', 'warning');
        schemaInput.focus();
        return;
    }

    const continueBtn = document.getElementById('continueSchemaBtn');
    setButtonLoading(continueBtn, true, 'Generating…');

    try {
        await generateSQL(schemaText);
    } finally {
        setButtonLoading(continueBtn, false, 'Continue SQL Generation', 'bolt');
    }
}

function renderSchemaCards(schemas) {
    const container = document.getElementById('relevantSchemaList');
    if (!container) return;
    container.innerHTML = '';

    schemas.forEach(t => {
        const card = document.createElement('div');
        card.className = 'table-schema-card';

        const sourceClass = t.source === 'user_provided' ? 'source-user' : 'source-live';
        const sourceLabel = t.source === 'user_provided' ? 'User-Provided Temporary Schema' : 'Live Database';

        let colsHtml = '';
        if (t.columns && t.columns.length > 0) {
            t.columns.forEach(col => {
                const isPk = t.primary_keys && t.primary_keys.includes(col.name);
                const pkBadge = isPk ? '<span class="pk-badge">PK</span>' : '';

                // Check FK
                let fkBadge = '';
                if (t.foreign_keys) {
                    const fk = t.foreign_keys.find(f => f.column === col.name);
                    if (fk) {
                        fkBadge = `<span class="fk-badge" title="FK to ${escapeHtml(fk.references_table)}.${escapeHtml(fk.references_column)}">FK → ${escapeHtml(fk.references_table)}.${escapeHtml(fk.references_column)}</span>`;
                    }
                }

                colsHtml += `
                    <tr>
                        <td><strong>${escapeHtml(col.name)}</strong>${pkBadge}${fkBadge}</td>
                        <td>${escapeHtml(col.type)}</td>
                        <td>${col.nullable ? 'NULL' : 'NOT NULL'}</td>
                    </tr>
                `;
            });
        }

        card.innerHTML = `
            <div class="table-schema-header">
                <span class="table-schema-name">${escapeHtml(t.name)}</span>
                <span class="schema-source-badge ${sourceClass}">${escapeHtml(sourceLabel)}</span>
            </div>
            <table class="schema-columns-table">
                <thead>
                    <tr>
                        <th>Column</th>
                        <th>Type</th>
                        <th>Nullability</th>
                    </tr>
                </thead>
                <tbody>
                    ${colsHtml}
                </tbody>
            </table>
        `;
        container.appendChild(card);
    });
}


// ══════════════════════════════════════════════════════════════
// Execute Query
// ══════════════════════════════════════════════════════════════

async function executeQuery() {
    if (!currentSQL || isExecuting) return;
    isExecuting = true;

    const btn = document.getElementById('executeBtn');
    setActionCardLoading(btn, true, 'Executing…');
    switchTab('results');

    try {
        const res = await fetch(`${API_BASE}/api/v1/query/execute`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sql: currentSQL }),
        });

        const container = document.getElementById('resultsContent');

        if (res.ok) {
            const data = await res.json();
            container.innerHTML = renderExecutionResults(data);
        } else {
            const errData = await parseErrorResponse(res);
            container.innerHTML = renderError(errData.message);
        }

    } catch (err) {
        document.getElementById('resultsContent').innerHTML =
            renderError('Failed to connect to backend. Ensure the server is running.');
    } finally {
        isExecuting = false;
        setActionCardLoading(btn, false, 'Execute Query');
    }
}

function renderExecutionResults(data) {
    if (!data.columns || data.columns.length === 0) {
        return '<div class="empty-state"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="empty-icon"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/></svg><p class="empty-title">No Results</p><p class="empty-desc">Query returned no results</p></div>';
    }

    let html = '<div class="results-meta">';
    html += `<span class="badge badge-count">${data.row_count} row${data.row_count !== 1 ? 's' : ''}</span>`;
    if (data.truncated) {
        html += `<span class="badge badge-truncated">⚠ Truncated (max ${data.max_rows})</span>`;
    }
    html += '</div>';

    html += '<div class="results-table-wrapper"><table class="results-table"><thead><tr>';
    data.columns.forEach(col => {
        html += `<th>${escapeHtml(col)}</th>`;
    });
    html += '</tr></thead><tbody>';

    data.rows.forEach(row => {
        html += '<tr>';
        row.forEach(val => {
            if (val === null || val === undefined) {
                html += '<td class="null-value">NULL</td>';
            } else {
                html += `<td>${escapeHtml(String(val))}</td>`;
            }
        });
        html += '</tr>';
    });

    html += '</tbody></table></div>';
    return html;
}

// ══════════════════════════════════════════════════════════════
// Analyze Query (EXPLAIN)
// ══════════════════════════════════════════════════════════════

async function analyzeQuery() {
    if (!currentSQL || isAnalyzing) return;
    isAnalyzing = true;

    const btn = document.getElementById('analyzeBtn');
    setActionCardLoading(btn, true, 'Analyzing…');
    switchTab('plan');

    try {
        const res = await fetch(`${API_BASE}/api/v1/query/explain`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sql: currentSQL }),
        });

        const container = document.getElementById('planContent');

        if (res.ok) {
            const data = await res.json();
            container.innerHTML = renderExplainResults(data);
        } else {
            const errData = await parseErrorResponse(res);
            container.innerHTML = renderError(errData.message);
        }

    } catch (err) {
        document.getElementById('planContent').innerHTML =
            renderError('Failed to connect to backend. Ensure the server is running.');
    } finally {
        isAnalyzing = false;
        setActionCardLoading(btn, false, 'Explain Query');
    }
}

// Alias explainQuery to analyzeQuery so both names work interchangeably
const explainQuery = analyzeQuery;

function renderExplainResults(data) {
    let html = '';

    // EXPLAIN plan table
    if (data.plan && data.plan.length > 0) {
        const planCols = ['id', 'select_type', 'table', 'type', 'possible_keys', 'key', 'rows', 'filtered', 'extra'];
        const planLabels = ['ID', 'Select Type', 'Table', 'Access Type', 'Possible Keys', 'Key Used', 'Est. Rows', 'Filtered %', 'Extra'];

        html += '<div class="plan-table-wrapper"><table class="plan-table"><thead><tr>';
        planLabels.forEach(label => {
            html += `<th>${label}</th>`;
        });
        html += '</tr></thead><tbody>';

        data.plan.forEach(row => {
            html += '<tr>';
            planCols.forEach(col => {
                const val = row[col];
                if (val === null || val === undefined) {
                    html += '<td style="color:var(--text-muted);font-style:italic;">—</td>';
                } else {
                    let displayVal = String(val);
                    // Highlight concerning access types
                    if (col === 'type' && displayVal.toUpperCase() === 'ALL') {
                        html += `<td style="color:var(--warning);font-weight:600;">${escapeHtml(displayVal)}</td>`;
                    } else {
                        html += `<td>${escapeHtml(displayVal)}</td>`;
                    }
                }
            });
            html += '</tr>';
        });

        html += '</tbody></table></div>';
    }

    // Analysis observations
    if (data.analysis && data.analysis.length > 0) {
        html += '<h4 style="font-size:0.82rem;color:var(--text-secondary);margin:16px 0 10px;font-weight:600;">Analysis Observations</h4>';
        html += '<ul class="analysis-list">';
        data.analysis.forEach(obs => {
            html += `<li>${escapeHtml(obs)}</li>`;
        });
        html += '</ul>';
    }

    if (!html) {
        return '<div class="empty-state"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="empty-icon"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg><p class="empty-title">No Plan Data</p><p class="empty-desc">No plan data available</p></div>';
    }

    return html;
}

// ══════════════════════════════════════════════════════════════
// Optimize Query
// ══════════════════════════════════════════════════════════════

async function optimizeQuery() {
    if (!currentSQL || isOptimizing) return;
    isOptimizing = true;

    const btn = document.getElementById('optimizeBtn');
    setActionCardLoading(btn, true, 'Optimizing…');
    switchTab('optimization');

    try {
        const res = await fetch(`${API_BASE}/api/v1/query/optimize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sql: currentSQL }),
        });

        const container = document.getElementById('optimizationContent');

        if (res.ok) {
            const data = await res.json();
            container.innerHTML = renderOptimizationResults(data);
        } else {
            const errData = await parseErrorResponse(res);
            container.innerHTML = renderError(errData.message);
        }

    } catch (err) {
        document.getElementById('optimizationContent').innerHTML =
            renderError('Failed to connect to backend. Ensure the server is running.');
    } finally {
        isOptimizing = false;
        setActionCardLoading(btn, false, 'Optimize Query');
    }
}

// ══════════════════════════════════════════════════════════════
// Analyze Performance (Phase 4E)
// ══════════════════════════════════════════════════════════════

/**
 * Called ONLY by the explicit "Analyze Performance" button click.
 * SQL generation does NOT call this function automatically.
 * Optimization does NOT call this function automatically.
 * EXPLAIN does NOT call this function automatically.
 */
async function analyzePerformance() {
    if (!currentSQL || isPerforming) return;
    isPerforming = true;

    const btn = document.getElementById('performanceBtn');
    setActionCardLoading(btn, true, 'Benchmarking…');
    switchTab('performance');

    try {
        const res = await fetch(`${API_BASE}/api/v1/query/performance`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sql: currentSQL, run_count: 5 }),
        });

        const container = document.getElementById('performanceContent');

        if (res.ok) {
            const data = await res.json();
            container.innerHTML = renderPerformanceResults(data);
        } else {
            const errData = await parseErrorResponse(res);
            container.innerHTML = renderError(errData.message);
        }

    } catch (err) {
        document.getElementById('performanceContent').innerHTML =
            renderError('Failed to connect to backend. Ensure the server is running.');
    } finally {
        isPerforming = false;
        setActionCardLoading(btn, false, 'Performance');
    }
}

function renderPerformanceResults(data) {
    if (!data || !data.timings_ms || data.timings_ms.length === 0) {
        return '<div class="empty-state"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="empty-icon"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg><p class="empty-title">No Timing Data</p><p class="empty-desc">No timing data available</p></div>';
    }

    let html = '';

    // Header summary card
    html += '<div class="perf-summary">';
    html += '<h4 style="font-size:0.78rem;color:var(--text-secondary);margin:0 0 14px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;">Performance Analysis</h4>';

    html += '<div class="perf-stats-grid">';

    html += `<div class="perf-stat">
        <div class="perf-stat-label">Runs</div>
        <div class="perf-stat-value">${data.run_count}</div>
    </div>`;

    html += `<div class="perf-stat perf-stat-highlight">
        <div class="perf-stat-label">Average</div>
        <div class="perf-stat-value">${data.average_ms.toFixed(3)} <span class="perf-unit">ms</span></div>
    </div>`;

    html += `<div class="perf-stat">
        <div class="perf-stat-label">Median</div>
        <div class="perf-stat-value">${data.median_ms.toFixed(3)} <span class="perf-unit">ms</span></div>
    </div>`;

    html += `<div class="perf-stat perf-stat-best">
        <div class="perf-stat-label">Minimum</div>
        <div class="perf-stat-value">${data.min_ms.toFixed(3)} <span class="perf-unit">ms</span></div>
    </div>`;

    html += `<div class="perf-stat perf-stat-worst">
        <div class="perf-stat-label">Maximum</div>
        <div class="perf-stat-value">${data.max_ms.toFixed(3)} <span class="perf-unit">ms</span></div>
    </div>`;

    if (data.warmup_runs > 0) {
        html += `<div class="perf-stat">
            <div class="perf-stat-label">Warmup Runs</div>
            <div class="perf-stat-value" style="font-size:0.9rem;">${data.warmup_runs} <span style="font-size:0.65rem;opacity:0.6;">(excluded)</span></div>
        </div>`;
    }

    html += '</div>'; // perf-stats-grid
    html += '</div>'; // perf-summary

    // Individual run timings
    html += '<h4 style="font-size:0.78rem;color:var(--text-secondary);margin:16px 0 10px;font-weight:600;">Individual Runs</h4>';
    html += '<div class="perf-runs">';

    const maxTime = data.max_ms || 1;
    data.timings_ms.forEach((t, i) => {
        const pct = Math.max(5, Math.round((t / maxTime) * 100));
        const isMin = (t === data.min_ms);
        const isMax = (t === data.max_ms);
        const cls = isMin ? 'perf-run-bar best' : (isMax ? 'perf-run-bar worst' : 'perf-run-bar');
        html += `<div class="perf-run-row">
            <div class="perf-run-label">Run ${i + 1}</div>
            <div class="perf-run-track">
                <div class="${cls}" style="width:${pct}%"></div>
            </div>
            <div class="perf-run-time">${t.toFixed(3)} ms${isMin ? ' ✓' : ''}${isMax ? ' ▲' : ''}</div>
        </div>`;
    });

    html += '</div>'; // perf-runs

    // Validation badge
    if (data.validation) {
        const valid = data.validation.valid;
        html += `<div style="margin-top:16px;">
            <span class="validation-badge ${valid ? 'valid' : 'invalid'}">
                ${valid ? '✓ SQL validated successfully' : '✗ SQL validation failed'}
            </span>
        </div>`;
    }

    return html;
}


function renderOptimizationResults(data) {
    let html = '';

    // Changed status
    if (data.changed) {
        html += '<div class="alert alert-success" style="margin-bottom:16px;">';
        html += '<span class="alert-icon">✓</span>';
        html += '<span>Optimization applied — the query structure was improved.</span>';
        html += '</div>';
    } else {
        html += '<div class="alert alert-warning" style="margin-bottom:16px;">';
        html += '<span class="alert-icon">ℹ</span>';
        html += '<span>No optimization changes were applicable to this query.</span>';
        html += '</div>';
    }

    // Side-by-side SQL comparison
    html += '<div class="optimization-comparison">';

    html += '<div class="opt-sql-card original">';
    html += '<div class="label">Original SQL</div>';
    html += `<div class="sql-block"><pre><code>${escapeHtml(data.original_sql)}</code></pre></div>`;
    html += '</div>';

    html += '<div class="opt-sql-card optimized">';
    html += '<div class="label">Optimized SQL</div>';
    html += `<div class="sql-block"><pre><code>${escapeHtml(data.optimized_sql)}</code></pre></div>`;
    html += '</div>';

    html += '</div>';

    // Optimization rules applied
    if (data.optimizations && data.optimizations.length > 0) {
        html += '<h4 style="font-size:0.82rem;color:var(--text-secondary);margin: 4px 0 10px;font-weight:600;">Optimization Rules Applied</h4>';
        html += '<ul class="optimization-rules">';
        data.optimizations.forEach(opt => {
            html += '<li>';
            html += `<span class="rule-name">${escapeHtml(opt.rule)}</span>`;
            html += escapeHtml(opt.description);
            html += '</li>';
        });
        html += '</ul>';
    }

    // Index recommendations
    if (data.index_recommendations && data.index_recommendations.length > 0) {
        html += '<div class="index-recommendations">';
        html += '<h4>Index Recommendations</h4>';
        html += '<ul>';
        data.index_recommendations.forEach(rec => {
            html += `<li>${escapeHtml(rec)}</li>`;
        });
        html += '</ul>';
        html += '</div>';
    }

    // Re-validation status
    if (data.optimized_validation) {
        const valid = data.optimized_validation.valid;
        html += '<div style="margin-top:16px;">';
        html += `<span class="validation-badge ${valid ? 'valid' : 'invalid'}">`;
        html += valid ? '✓ Optimized SQL validated successfully' : '✗ Optimized SQL validation failed';
        html += '</span>';
        html += '</div>';
    }

    return html;
}

// ══════════════════════════════════════════════════════════════
// Tab Management
// ══════════════════════════════════════════════════════════════

function switchTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    // Update tab panels
    document.querySelectorAll('.tab-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === `tab-${tabName}`);
    });
}

// ══════════════════════════════════════════════════════════════
// Action Panel Visibility
// ══════════════════════════════════════════════════════════════

function showActionPanel() {
    const panel = document.getElementById('actionPanel');
    if (panel) panel.classList.remove('hidden');
}

function hideActionPanel() {
    const panel = document.getElementById('actionPanel');
    if (panel) panel.classList.add('hidden');
}

function hideWelcomePanel() {
    const panel = document.getElementById('welcomePanel');
    if (panel) panel.classList.add('hidden');
}

// ══════════════════════════════════════════════════════════════
// Alerts
// ══════════════════════════════════════════════════════════════

function showAlert(message, type = 'error') {
    const area = document.getElementById('alertArea');

    const icons = { error: '⚠', warning: '⚡', success: '✓' };
    const icon = icons[type] || icons.error;

    const alertEl = document.createElement('div');
    alertEl.className = `alert alert-${type}`;
    alertEl.innerHTML = `
        <span class="alert-icon">${icon}</span>
        <span>${escapeHtml(message)}</span>
        <button class="alert-dismiss" onclick="this.parentElement.remove()">✕</button>
    `;

    area.innerHTML = '';
    area.appendChild(alertEl);

    // Auto-dismiss after 8 seconds
    setTimeout(() => {
        if (alertEl.parentElement) alertEl.remove();
    }, 8000);
}

function clearAlerts() {
    document.getElementById('alertArea').innerHTML = '';
}

// ══════════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════════

/**
 * Set loading state for standard buttons (e.g., Generate SQL).
 */
function setButtonLoading(btn, loading, text, iconType) {
    btn.disabled = loading;
    if (loading) {
        btn.innerHTML = `<span class="spinner"></span> ${escapeHtml(text)}`;
    } else {
        // Restore button with icon
        const iconSvg = getSvgIcon(iconType);
        btn.innerHTML = iconSvg + ' ' + escapeHtml(text);
    }
}

/**
 * Set loading state for action cards.
 */
function setActionCardLoading(btn, loading, text) {
    btn.disabled = loading;
    if (loading) {
        const actionInfo = btn.querySelector('.action-info');
        if (actionInfo) {
            const nameEl = actionInfo.querySelector('.action-name');
            if (nameEl) nameEl.innerHTML = `<span class="spinner" style="border-color:rgba(0,0,0,0.15);border-top-color:currentColor;"></span> ${escapeHtml(text)}`;
        }
    } else {
        const actionInfo = btn.querySelector('.action-info');
        if (actionInfo) {
            const nameEl = actionInfo.querySelector('.action-name');
            if (nameEl) nameEl.textContent = text;
        }
    }
}

function getSvgIcon(type) {
    if (type === 'bolt') {
        return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>';
    }
    return '';
}

function resetResults() {
    clearAlerts();

    // Hide schema clarification and source banners
    const missingSection = document.getElementById('missingSchemaSection');
    if (missingSection) missingSection.classList.add('hidden');
    const schemaSection = document.getElementById('schemaSection');
    if (schemaSection) schemaSection.classList.add('hidden');
    const banner = document.getElementById('schemaSourceBanner');
    if (banner) banner.classList.add('hidden');

    // Reset results tab
    document.getElementById('resultsContent').innerHTML =
        '<div class="empty-state"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="empty-icon"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/></svg><p class="empty-title">No Results Yet</p><p class="empty-desc">Click "Execute Query" to run the SQL and see results</p></div>';

    // Reset plan tab
    document.getElementById('planContent').innerHTML =
        '<div class="empty-state"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="empty-icon"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg><p class="empty-title">No Query Plan</p><p class="empty-desc">Click "Explain Query" to view the MySQL execution plan</p></div>';

    // Reset optimization tab
    document.getElementById('optimizationContent').innerHTML =
        '<div class="empty-state"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="empty-icon"><path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"/><path d="M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"/></svg><p class="empty-title">No Optimizations</p><p class="empty-desc">Click "Optimize Query" to analyze for improvements</p></div>';

    // Reset performance tab
    document.getElementById('performanceContent').innerHTML =
        '<div class="empty-state"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="empty-icon"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg><p class="empty-title">No Performance Data</p><p class="empty-desc">Click "Performance" to benchmark this query</p></div>';

    // Switch to results tab
    switchTab('results');
}

/**
 * Copy generated SQL to system clipboard with user feedback.
 */
function copySQL() {
    if (!currentSQL) return;

    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(currentSQL).then(() => {
            showCopyFeedback();
        }).catch(() => {
            fallbackCopyText(currentSQL);
        });
    } else {
        fallbackCopyText(currentSQL);
    }
}

function fallbackCopyText(text) {
    try {
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        textArea.style.top = '-999999px';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        document.execCommand('copy');
        textArea.remove();
        showCopyFeedback();
    } catch {
        showAlert('Could not copy SQL to clipboard automatically.', 'warning');
    }
}

function showCopyFeedback() {
    const textEl = document.getElementById('copyBtnText');
    if (textEl) {
        const orig = textEl.textContent;
        textEl.textContent = 'Copied!';
        setTimeout(() => {
            textEl.textContent = orig;
        }, 2000);
    }
}

function sanitizeErrorMessage(msg) {
    if (!msg || typeof msg !== 'string') return 'An error occurred.';
    // Strip any inadvertent passwords, tokens, or raw paths
    return msg
        .replace(/(password|pwd|pass|secret|token)\s*=\s*[^\s,;]+/gi, '$1=***')
        .replace(/(AIza[0-9A-Za-z-_]{35})/g, '***');
}

function renderError(message) {
    const safeMsg = sanitizeErrorMessage(message);
    return `<div class="alert alert-error">
        <span class="alert-icon">⚠</span>
        <span>${escapeHtml(safeMsg)}</span>
    </div>`;
}

async function parseErrorResponse(res) {
    try {
        const data = await res.json();
        // FastAPI HTTPException wraps detail
        if (data.detail) {
            if (typeof data.detail === 'object' && data.detail.message) {
                return { message: data.detail.message, code: data.detail.error_code || '' };
            }
            if (typeof data.detail === 'string') {
                return { message: data.detail, code: '' };
            }
            // Pydantic validation errors (array of detail objects)
            if (Array.isArray(data.detail)) {
                const msgs = data.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
                return { message: msgs, code: 'VALIDATION_ERROR' };
            }
        }
        return { message: `Request failed with status ${res.status}`, code: '' };
    } catch {
        return { message: `Request failed with status ${res.status}`, code: '' };
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}
