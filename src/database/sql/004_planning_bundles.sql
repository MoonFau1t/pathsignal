CREATE TABLE user_profile_snapshots (
    profile_snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    source_path TEXT,
    source_file_hash TEXT,
    schema_version TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE planning_bundles (
    planning_bundle_id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_snapshot_id INTEGER NOT NULL,
    input_fingerprint TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    generation_mode TEXT,
    model_provider TEXT,
    model_name TEXT,
    prompt_version TEXT,
    planning_context_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (input_fingerprint, output_hash),
    FOREIGN KEY (profile_snapshot_id)
        REFERENCES user_profile_snapshots(profile_snapshot_id)
);

CREATE TABLE planning_target_career_paths (
    career_path_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    planning_bundle_id INTEGER NOT NULL,
    path_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    path_type_or_tier TEXT,
    name_or_title TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (planning_bundle_id, path_id),
    FOREIGN KEY (planning_bundle_id)
        REFERENCES planning_bundles(planning_bundle_id)
        ON DELETE CASCADE
);

CREATE TABLE planning_search_queries (
    search_query_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    planning_bundle_id INTEGER NOT NULL,
    career_path_row_id INTEGER NOT NULL,
    query_identity TEXT NOT NULL,
    position INTEGER NOT NULL,
    query_text TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (planning_bundle_id, career_path_row_id, query_identity),
    FOREIGN KEY (planning_bundle_id)
        REFERENCES planning_bundles(planning_bundle_id)
        ON DELETE CASCADE,
    FOREIGN KEY (career_path_row_id)
        REFERENCES planning_target_career_paths(career_path_row_id)
        ON DELETE CASCADE
);

CREATE TABLE planning_search_plans (
    search_plan_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    planning_bundle_id INTEGER NOT NULL,
    career_path_row_id INTEGER NOT NULL,
    search_query_row_id INTEGER NOT NULL,
    plan_identity TEXT NOT NULL,
    position INTEGER NOT NULL,
    provider TEXT,
    mode TEXT,
    query_text TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (planning_bundle_id, search_query_row_id, plan_identity),
    FOREIGN KEY (planning_bundle_id)
        REFERENCES planning_bundles(planning_bundle_id)
        ON DELETE CASCADE,
    FOREIGN KEY (career_path_row_id)
        REFERENCES planning_target_career_paths(career_path_row_id)
        ON DELETE CASCADE,
    FOREIGN KEY (search_query_row_id)
        REFERENCES planning_search_queries(search_query_row_id)
        ON DELETE CASCADE
);

CREATE TABLE planning_artifacts (
    planning_artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    planning_bundle_id INTEGER NOT NULL,
    artifact_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (planning_bundle_id, artifact_type, file_path, content_hash),
    FOREIGN KEY (planning_bundle_id)
        REFERENCES planning_bundles(planning_bundle_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_planning_bundles_profile_snapshot_id
ON planning_bundles (profile_snapshot_id);

CREATE INDEX idx_planning_bundles_input_fingerprint
ON planning_bundles (input_fingerprint);

CREATE INDEX idx_planning_target_career_paths_bundle_id
ON planning_target_career_paths (planning_bundle_id);

CREATE INDEX idx_planning_search_queries_bundle_id
ON planning_search_queries (planning_bundle_id);

CREATE INDEX idx_planning_search_queries_career_path_row_id
ON planning_search_queries (career_path_row_id);

CREATE INDEX idx_planning_search_plans_bundle_id
ON planning_search_plans (planning_bundle_id);

CREATE INDEX idx_planning_search_plans_search_query_row_id
ON planning_search_plans (search_query_row_id);

CREATE INDEX idx_planning_artifacts_bundle_id
ON planning_artifacts (planning_bundle_id);
