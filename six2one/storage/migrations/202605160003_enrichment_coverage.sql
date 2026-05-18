CREATE TABLE enrichment_coverage (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    dependency TEXT NOT NULL,
    state TEXT NOT NULL,
    enriched_at TEXT,
    expires_at TEXT,
    source_run_id TEXT,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (scope, key, dependency),
    FOREIGN KEY (source_run_id) REFERENCES source_runs(id) ON DELETE SET NULL
);
CREATE INDEX idx_enrichment_dependency ON enrichment_coverage(dependency, state);
