CREATE TABLE queue_jobs (
    id TEXT PRIMARY KEY,
    source_run_id TEXT,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    available_at TEXT,
    leased_at TEXT,
    lease_expires_at TEXT,
    locked_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT,
    last_error TEXT,
    FOREIGN KEY (source_run_id) REFERENCES source_runs(id) ON DELETE SET NULL
);
CREATE INDEX idx_queue_jobs_claim ON queue_jobs(state, available_at, priority, created_at);
CREATE INDEX idx_queue_jobs_source_run ON queue_jobs(source_run_id);
CREATE INDEX idx_queue_jobs_kind ON queue_jobs(kind);

CREATE TABLE queue_job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    event TEXT NOT NULL,
    message TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES queue_jobs(id) ON DELETE CASCADE
);
