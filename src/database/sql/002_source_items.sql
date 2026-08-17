CREATE TABLE source_items (
    source_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    provider TEXT,
    external_id TEXT,
    title TEXT NOT NULL,
    organization TEXT,
    url TEXT,
    canonical_url TEXT,
    published_at TEXT,
    raw_text TEXT,
    payload_json TEXT NOT NULL,
    processing_status TEXT NOT NULL DEFAULT 'pending',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_source_items_source_type
ON source_items (source_type);

CREATE INDEX idx_source_items_processing_status
ON source_items (processing_status);

CREATE INDEX idx_source_items_last_seen_at
ON source_items (last_seen_at);

CREATE INDEX idx_source_items_canonical_url
ON source_items (canonical_url);
