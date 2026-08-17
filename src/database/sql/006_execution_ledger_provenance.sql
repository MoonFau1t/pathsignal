CREATE TABLE run_search_plan_statuses (
    run_search_plan_status_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    planning_search_plan_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    skip_reason TEXT,
    selection_order INTEGER,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (run_id, planning_search_plan_id),
    CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    CHECK (selection_order IS NULL OR selection_order >= 0),
    CHECK (
        (status = 'skipped' AND skip_reason IS NOT NULL AND length(trim(skip_reason)) > 0)
        OR (status <> 'skipped' AND skip_reason IS NULL)
    ),
    FOREIGN KEY (run_id)
        REFERENCES pipeline_runs(run_id)
        ON DELETE CASCADE,
    FOREIGN KEY (planning_search_plan_id)
        REFERENCES planning_search_plans(search_plan_row_id)
);

CREATE TABLE source_executions (
    source_execution_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    planning_search_plan_id INTEGER,
    source_type TEXT NOT NULL,
    provider TEXT,
    source_key TEXT,
    source_name TEXT,
    source_locator TEXT,
    execution_mode TEXT,
    status TEXT NOT NULL,
    requested_result_limit INTEGER,
    returned_item_count INTEGER,
    discovered_item_count INTEGER,
    request_fingerprint TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_type TEXT,
    error_message TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (status IN ('running', 'completed', 'failed')),
    CHECK (length(trim(source_type)) > 0),
    CHECK (requested_result_limit IS NULL OR requested_result_limit >= 0),
    CHECK (returned_item_count IS NULL OR returned_item_count >= 0),
    CHECK (discovered_item_count IS NULL OR discovered_item_count >= 0),
    FOREIGN KEY (run_id)
        REFERENCES pipeline_runs(run_id)
        ON DELETE CASCADE,
    FOREIGN KEY (planning_search_plan_id)
        REFERENCES planning_search_plans(search_plan_row_id)
);

CREATE TABLE source_item_discoveries (
    source_execution_id INTEGER NOT NULL,
    source_item_id INTEGER NOT NULL,
    result_position INTEGER,
    discovered_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (source_execution_id, source_item_id),
    CHECK (result_position IS NULL OR result_position >= 0),
    FOREIGN KEY (source_execution_id)
        REFERENCES source_executions(source_execution_id)
        ON DELETE CASCADE,
    FOREIGN KEY (source_item_id)
        REFERENCES source_items(source_item_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_run_search_plan_statuses_run_status
ON run_search_plan_statuses (run_id, status, planning_search_plan_id);

CREATE INDEX idx_run_search_plan_statuses_plan_id
ON run_search_plan_statuses (planning_search_plan_id);

CREATE INDEX idx_source_executions_run_status
ON source_executions (run_id, status, source_execution_id);

CREATE INDEX idx_source_executions_plan_id
ON source_executions (planning_search_plan_id);

CREATE UNIQUE INDEX uq_source_executions_run_plan
ON source_executions (run_id, planning_search_plan_id)
WHERE planning_search_plan_id IS NOT NULL;

CREATE INDEX idx_source_item_discoveries_source_item_id
ON source_item_discoveries (source_item_id, source_execution_id);
