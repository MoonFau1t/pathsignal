CREATE TABLE filter_executions (
    filter_execution_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    execution_mode TEXT,
    provider TEXT,
    model TEXT,
    prompt_version TEXT,
    prompt_fingerprint TEXT,
    input_fingerprint TEXT,
    item_count INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_type TEXT,
    error_message TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (filter_execution_id, run_id),
    CHECK (status IN ('running', 'completed', 'failed')),
    CHECK (item_count > 0),
    CHECK (length(metadata_json) <= 10000),
    CHECK (
        (status = 'running'
            AND completed_at IS NULL
            AND error_type IS NULL
            AND error_message IS NULL)
        OR (status = 'completed'
            AND completed_at IS NOT NULL
            AND error_type IS NULL
            AND error_message IS NULL)
        OR (status = 'failed'
            AND completed_at IS NOT NULL
            AND error_type IS NOT NULL
            AND length(trim(error_type)) > 0
            AND error_message IS NOT NULL
            AND length(trim(error_message)) > 0)
    ),
    FOREIGN KEY (run_id)
        REFERENCES pipeline_runs(run_id)
        ON DELETE CASCADE
);

CREATE TABLE run_source_item_filter_statuses (
    run_source_item_filter_status_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    source_item_id INTEGER NOT NULL,
    filter_execution_id INTEGER,
    status TEXT NOT NULL,
    deferred_reason TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (run_id, source_item_id),
    CHECK (
        status IN (
            'pending',
            'running',
            'accepted',
            'rejected',
            'deferred',
            'failed'
        )
    ),
    CHECK (length(metadata_json) <= 10000),
    CHECK (
        (status = 'pending'
            AND filter_execution_id IS NULL
            AND deferred_reason IS NULL
            AND started_at IS NULL
            AND completed_at IS NULL)
        OR (status = 'deferred'
            AND filter_execution_id IS NULL
            AND deferred_reason IS NOT NULL
            AND length(trim(deferred_reason)) > 0
            AND started_at IS NULL
            AND completed_at IS NOT NULL)
        OR (status = 'running'
            AND filter_execution_id IS NOT NULL
            AND deferred_reason IS NULL
            AND started_at IS NOT NULL
            AND completed_at IS NULL)
        OR (status IN ('accepted', 'rejected', 'failed')
            AND filter_execution_id IS NOT NULL
            AND deferred_reason IS NULL
            AND started_at IS NOT NULL
            AND completed_at IS NOT NULL)
    ),
    FOREIGN KEY (run_id)
        REFERENCES pipeline_runs(run_id)
        ON DELETE CASCADE,
    FOREIGN KEY (source_item_id)
        REFERENCES source_items(source_item_id)
        ON DELETE CASCADE,
    FOREIGN KEY (filter_execution_id)
        REFERENCES filter_executions(filter_execution_id)
);

CREATE UNIQUE INDEX uq_run_filter_status_execution_membership
ON run_source_item_filter_statuses (
    filter_execution_id,
    run_id,
    source_item_id
);

CREATE TABLE filter_decisions (
    filter_decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    filter_execution_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    source_item_id INTEGER NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    confidence REAL,
    matched_career_path_ids_json TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (filter_execution_id, source_item_id),
    UNIQUE (run_id, source_item_id),
    CHECK (decision IN ('accepted', 'rejected')),
    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    CHECK (length(metadata_json) <= 10000),
    CHECK (
        matched_career_path_ids_json IS NULL
        OR length(matched_career_path_ids_json) <= 10000
    ),
    FOREIGN KEY (filter_execution_id)
        REFERENCES filter_executions(filter_execution_id)
        ON DELETE CASCADE,
    FOREIGN KEY (run_id)
        REFERENCES pipeline_runs(run_id)
        ON DELETE CASCADE,
    FOREIGN KEY (source_item_id)
        REFERENCES source_items(source_item_id)
        ON DELETE CASCADE,
    FOREIGN KEY (filter_execution_id, run_id, source_item_id)
        REFERENCES run_source_item_filter_statuses (
            filter_execution_id,
            run_id,
            source_item_id
        )
);

CREATE INDEX idx_run_filter_statuses_run_status
ON run_source_item_filter_statuses (run_id, status, source_item_id);

CREATE INDEX idx_run_filter_statuses_source_item
ON run_source_item_filter_statuses (source_item_id, run_id);

CREATE INDEX idx_run_filter_statuses_execution
ON run_source_item_filter_statuses (filter_execution_id, source_item_id);

CREATE INDEX idx_filter_executions_run_status
ON filter_executions (run_id, status, filter_execution_id);

CREATE INDEX idx_filter_decisions_run
ON filter_decisions (run_id, source_item_id, filter_decision_id);

CREATE INDEX idx_filter_decisions_source_item
ON filter_decisions (source_item_id, filter_decision_id DESC);
