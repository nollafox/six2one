CREATE TABLE storage_metadata (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (namespace, key)
);

CREATE TABLE source_runs (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    state TEXT NOT NULL,
    backend TEXT,
    total_candidates INTEGER,
    total_matches INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE posts (
    id INTEGER PRIMARY KEY,
    rating TEXT,
    created_at TEXT,
    updated_at TEXT,
    file_url TEXT,
    file_ext TEXT,
    file_size INTEGER,
    file_width INTEGER,
    file_height INTEGER,
    file_md5 TEXT,
    score_total INTEGER,
    fav_count INTEGER,
    comment_count INTEGER,
    flags_deleted INTEGER NOT NULL DEFAULT 0,
    flags_pending INTEGER NOT NULL DEFAULT 0,
    flags_flagged INTEGER NOT NULL DEFAULT 0,
    uploader_id INTEGER,
    uploader_name TEXT,
    raw_json TEXT NOT NULL,
    cached_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE post_tags (
    post_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (post_id, category, tag),
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);
CREATE INDEX idx_post_tags_tag ON post_tags(tag);
CREATE INDEX idx_post_tags_post ON post_tags(post_id);

CREATE TABLE post_sources (
    post_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (post_id, source),
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE TABLE post_pools (
    post_id INTEGER NOT NULL,
    pool_id INTEGER NOT NULL,
    PRIMARY KEY (post_id, pool_id),
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE TABLE images (
    post_id INTEGER PRIMARY KEY,
    file_url TEXT NOT NULL,
    destination TEXT,
    state TEXT NOT NULL,
    bytes_written INTEGER,
    checksum TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);
