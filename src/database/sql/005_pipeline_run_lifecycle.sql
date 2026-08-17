ALTER TABLE pipeline_runs
ADD COLUMN planning_bundle_id INTEGER
REFERENCES planning_bundles(planning_bundle_id);

ALTER TABLE pipeline_runs
ADD COLUMN execution_mode TEXT;

ALTER TABLE pipeline_runs
ADD COLUMN failure_stage TEXT;

ALTER TABLE pipeline_runs
ADD COLUMN error_type TEXT;

ALTER TABLE pipeline_runs
ADD COLUMN error_message TEXT;

ALTER TABLE pipeline_runs
ADD COLUMN updated_at TEXT;

CREATE INDEX idx_pipeline_runs_status_started_at
ON pipeline_runs (status, started_at DESC, run_id DESC);

CREATE INDEX idx_pipeline_runs_planning_bundle_id
ON pipeline_runs (planning_bundle_id);
