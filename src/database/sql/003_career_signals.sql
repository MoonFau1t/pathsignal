CREATE TABLE career_signals (
    career_signal_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL UNIQUE,
    source_item_id INTEGER,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    organization TEXT,
    url TEXT,
    published_at TEXT,
    summary TEXT,
    source_type TEXT NOT NULL,
    relevance_score REAL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id)
        REFERENCES source_items(source_item_id)
        ON DELETE SET NULL
);

CREATE INDEX idx_career_signals_category
ON career_signals (category);

CREATE INDEX idx_career_signals_source_type
ON career_signals (source_type);

CREATE INDEX idx_career_signals_source_item_id
ON career_signals (source_item_id);

CREATE INDEX idx_career_signals_updated_at
ON career_signals (updated_at);
