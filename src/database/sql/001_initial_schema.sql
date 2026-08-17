CREATE TABLE pipeline_runs (
    run_id TEXT PRIMARY KEY,
    pipeline_version TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    summary_json TEXT,
    metadata_json TEXT
);
