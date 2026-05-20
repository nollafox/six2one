CREATE TABLE schema_metadata (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_ms INTEGER NOT NULL,
    PRIMARY KEY (namespace, key)
) STRICT, WITHOUT ROWID;

CREATE TABLE source_runs (
    source_run_id INTEGER PRIMARY KEY,
    query TEXT NOT NULL,
    state_id INTEGER NOT NULL,
    backend_id INTEGER,
    total_candidates INTEGER,
    total_matches INTEGER,
    created_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL,
    completed_ms INTEGER
) STRICT;

CREATE INDEX source_runs_by_state
ON source_runs(state_id, updated_ms, source_run_id);

CREATE TABLE raw_payloads (
    entity_kind_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    cached_ms INTEGER NOT NULL,
    PRIMARY KEY (entity_kind_id, entity_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE raw_text_payloads (
    entity_kind_id INTEGER NOT NULL,
    external_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    cached_ms INTEGER NOT NULL,
    PRIMARY KEY (entity_kind_id, external_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE file_extensions (
    file_ext_id INTEGER PRIMARY KEY,
    extension TEXT NOT NULL UNIQUE
) STRICT;

CREATE TABLE posts (
    post_id INTEGER PRIMARY KEY,
    rating_id INTEGER NOT NULL,
    source_created_ms INTEGER,
    source_updated_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    file_ext_id INTEGER,
    file_size_bytes INTEGER,
    file_width INTEGER,
    file_height INTEGER,
    file_md5 BLOB,
    score_total INTEGER NOT NULL DEFAULT 0,
    favorite_count INTEGER NOT NULL DEFAULT 0,
    comment_count INTEGER NOT NULL DEFAULT 0,
    uploader_id INTEGER,
    approver_id INTEGER,
    parent_post_id INTEGER,
    child_count INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    flags INTEGER NOT NULL DEFAULT 0,
    CHECK (rating_id >= 0),
    CHECK (favorite_count >= 0),
    CHECK (comment_count >= 0),
    CHECK (child_count >= 0),
    FOREIGN KEY (file_ext_id) REFERENCES file_extensions(file_ext_id) ON DELETE RESTRICT
) STRICT;

CREATE INDEX posts_by_rating_created
ON posts(rating_id, source_created_ms DESC, post_id);

CREATE INDEX posts_by_score
ON posts(score_total DESC, post_id);

CREATE INDEX posts_by_favorites
ON posts(favorite_count DESC, post_id);

CREATE INDEX posts_by_md5
ON posts(file_md5, post_id)
WHERE file_md5 IS NOT NULL;

CREATE INDEX posts_by_parent
ON posts(parent_post_id, post_id)
WHERE parent_post_id IS NOT NULL;

CREATE TABLE post_details (
    post_id INTEGER PRIMARY KEY,
    description TEXT,
    sample_url TEXT,
    sample_width INTEGER,
    sample_height INTEGER,
    preview_url TEXT,
    preview_width INTEGER,
    preview_height INTEGER,
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT;

CREATE TABLE tags (
    tag_id INTEGER PRIMARY KEY,
    source_tag_id INTEGER UNIQUE,
    name TEXT NOT NULL UNIQUE,
    normalized_name TEXT NOT NULL UNIQUE,
    category_id INTEGER NOT NULL,
    post_count INTEGER NOT NULL DEFAULT 0,
    flags INTEGER NOT NULL DEFAULT 0,
    created_ms INTEGER,
    updated_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    CHECK (post_count >= 0)
) STRICT;

CREATE INDEX tags_by_category_count
ON tags(category_id, post_count DESC, tag_id);

CREATE TABLE post_tag_edges (
    tag_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    PRIMARY KEY (tag_id, post_id),
    FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE,
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE INDEX post_tag_edges_by_post
ON post_tag_edges(post_id, tag_id);

CREATE TABLE tag_aliases (
    antecedent_tag_id INTEGER NOT NULL,
    consequent_tag_id INTEGER NOT NULL,
    status_id INTEGER NOT NULL,
    created_ms INTEGER,
    updated_ms INTEGER,
    creator_id INTEGER,
    approver_id INTEGER,
    reason TEXT,
    PRIMARY KEY (antecedent_tag_id, status_id, consequent_tag_id),
    FOREIGN KEY (antecedent_tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE,
    FOREIGN KEY (consequent_tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE INDEX tag_aliases_by_consequent
ON tag_aliases(consequent_tag_id, status_id, antecedent_tag_id);

CREATE TABLE tag_implications (
    antecedent_tag_id INTEGER NOT NULL,
    consequent_tag_id INTEGER NOT NULL,
    status_id INTEGER NOT NULL,
    created_ms INTEGER,
    updated_ms INTEGER,
    creator_id INTEGER,
    approver_id INTEGER,
    reason TEXT,
    PRIMARY KEY (antecedent_tag_id, consequent_tag_id),
    FOREIGN KEY (antecedent_tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE,
    FOREIGN KEY (consequent_tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE INDEX tag_implications_by_consequent
ON tag_implications(consequent_tag_id, antecedent_tag_id);

CREATE INDEX tag_implications_active
ON tag_implications(antecedent_tag_id, consequent_tag_id)
WHERE status_id = 1;

CREATE TABLE tag_implication_closure (
    antecedent_tag_id INTEGER NOT NULL,
    consequent_tag_id INTEGER NOT NULL,
    depth INTEGER NOT NULL,
    via_tag_id INTEGER,
    PRIMARY KEY (antecedent_tag_id, consequent_tag_id),
    FOREIGN KEY (antecedent_tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE,
    FOREIGN KEY (consequent_tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE,
    FOREIGN KEY (via_tag_id) REFERENCES tags(tag_id) ON DELETE SET NULL,
    CHECK (depth >= 1)
) STRICT, WITHOUT ROWID;

CREATE INDEX tag_implication_closure_by_consequent
ON tag_implication_closure(consequent_tag_id, antecedent_tag_id);

CREATE TABLE sources (
    source_id INTEGER PRIMARY KEY,
    source_hash BLOB NOT NULL UNIQUE,
    source_url TEXT NOT NULL
) STRICT;

CREATE TABLE post_source_edges (
    post_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    PRIMARY KEY (post_id, source_id),
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(source_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE INDEX post_source_edges_by_source
ON post_source_edges(source_id, post_id);

CREATE TABLE image_variants (
    variant_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
) STRICT;

INSERT INTO image_variants (variant_id, name)
VALUES (1, 'original'), (2, 'sample'), (3, 'preview');

CREATE TABLE post_files (
    post_id INTEGER NOT NULL,
    variant_id INTEGER NOT NULL,
    source_id INTEGER,
    local_path TEXT,
    file_ext_id INTEGER,
    width INTEGER,
    height INTEGER,
    size_bytes INTEGER,
    md5 BLOB,
    download_state_id INTEGER NOT NULL,
    bytes_written INTEGER,
    checksum BLOB,
    downloaded_ms INTEGER,
    created_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL,
    PRIMARY KEY (post_id, variant_id),
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE,
    FOREIGN KEY (variant_id) REFERENCES image_variants(variant_id) ON DELETE RESTRICT,
    FOREIGN KEY (source_id) REFERENCES sources(source_id) ON DELETE SET NULL,
    FOREIGN KEY (file_ext_id) REFERENCES file_extensions(file_ext_id) ON DELETE RESTRICT
) STRICT, WITHOUT ROWID;

CREATE INDEX post_files_by_download_state
ON post_files(download_state_id, updated_ms, post_id, variant_id)
WHERE download_state_id IN (0, 1, 3);

CREATE INDEX post_files_by_md5
ON post_files(md5, post_id, variant_id)
WHERE md5 IS NOT NULL;

CREATE TABLE collections (
    collection_kind_id INTEGER NOT NULL,
    collection_id INTEGER NOT NULL,
    name TEXT,
    normalized_name TEXT,
    shortname TEXT,
    category_id INTEGER,
    post_count INTEGER NOT NULL DEFAULT 0,
    creator_id INTEGER,
    cached_ms INTEGER NOT NULL,
    PRIMARY KEY (collection_kind_id, collection_id),
    CHECK (post_count >= 0)
) STRICT, WITHOUT ROWID;

CREATE INDEX collections_by_name
ON collections(collection_kind_id, normalized_name, collection_id)
WHERE normalized_name IS NOT NULL;

CREATE TABLE collection_details (
    collection_kind_id INTEGER NOT NULL,
    collection_id INTEGER NOT NULL,
    description TEXT,
    PRIMARY KEY (collection_kind_id, collection_id),
    FOREIGN KEY (collection_kind_id, collection_id)
        REFERENCES collections(collection_kind_id, collection_id)
        ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE TABLE collection_post_edges (
    collection_kind_id INTEGER NOT NULL,
    collection_id INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    PRIMARY KEY (collection_kind_id, collection_id, sequence, post_id),
    FOREIGN KEY (collection_kind_id, collection_id)
        REFERENCES collections(collection_kind_id, collection_id)
        ON DELETE CASCADE,
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE INDEX collection_post_edges_by_post
ON collection_post_edges(post_id, collection_kind_id, collection_id);

CREATE TABLE users (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    normalized_name TEXT UNIQUE,
    cached_ms INTEGER NOT NULL
) STRICT;

CREATE TABLE artists (
    artist_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    normalized_name TEXT NOT NULL UNIQUE,
    flags INTEGER NOT NULL DEFAULT 0,
    cached_ms INTEGER NOT NULL
) STRICT;

CREATE TABLE artist_urls (
    artist_id INTEGER NOT NULL,
    url_hash BLOB NOT NULL,
    url TEXT NOT NULL,
    cached_ms INTEGER NOT NULL,
    PRIMARY KEY (artist_id, url_hash),
    FOREIGN KEY (artist_id) REFERENCES artists(artist_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE INDEX artist_urls_by_hash
ON artist_urls(url_hash, artist_id);

CREATE TABLE artist_versions (
    artist_version_id INTEGER PRIMARY KEY,
    artist_id INTEGER,
    updater_id INTEGER,
    name TEXT,
    normalized_name TEXT,
    created_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    FOREIGN KEY (artist_id) REFERENCES artists(artist_id) ON DELETE SET NULL
) STRICT;

CREATE INDEX artist_versions_by_artist
ON artist_versions(artist_id, created_ms DESC, artist_version_id)
WHERE artist_id IS NOT NULL;

CREATE TABLE comments (
    comment_id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL,
    user_id INTEGER,
    score INTEGER,
    created_ms INTEGER,
    updated_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX comments_by_post
ON comments(post_id, created_ms DESC, comment_id);

CREATE INDEX comments_by_user
ON comments(user_id, created_ms DESC, comment_id)
WHERE user_id IS NOT NULL;

CREATE TABLE comment_text (
    comment_id INTEGER PRIMARY KEY,
    body TEXT,
    FOREIGN KEY (comment_id) REFERENCES comments(comment_id) ON DELETE CASCADE
) STRICT;

CREATE TABLE notes (
    note_id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL,
    user_id INTEGER,
    x INTEGER,
    y INTEGER,
    width INTEGER,
    height INTEGER,
    created_ms INTEGER,
    updated_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX notes_by_post
ON notes(post_id, note_id);

CREATE TABLE note_text (
    note_id INTEGER PRIMARY KEY,
    body TEXT,
    FOREIGN KEY (note_id) REFERENCES notes(note_id) ON DELETE CASCADE
) STRICT;

CREATE TABLE note_versions (
    note_version_id INTEGER PRIMARY KEY,
    note_id INTEGER,
    post_id INTEGER NOT NULL,
    user_id INTEGER,
    created_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    FOREIGN KEY (note_id) REFERENCES notes(note_id) ON DELETE SET NULL,
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX note_versions_by_post
ON note_versions(post_id, created_ms DESC, note_version_id);

CREATE TABLE post_votes (
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    score INTEGER NOT NULL,
    created_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    PRIMARY KEY (post_id, user_id),
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE INDEX post_votes_by_user
ON post_votes(user_id, post_id);

CREATE TABLE favorites (
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    created_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    PRIMARY KEY (post_id, user_id),
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE INDEX favorites_by_user
ON favorites(user_id, post_id);

CREATE TABLE post_flags (
    post_flag_id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL,
    user_id INTEGER,
    reason_id INTEGER,
    created_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX post_flags_by_post
ON post_flags(post_id, created_ms DESC, post_flag_id);

CREATE TABLE post_events (
    post_event_id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL,
    event_kind_id INTEGER NOT NULL,
    user_id INTEGER,
    created_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX post_events_by_post
ON post_events(post_id, event_kind_id, created_ms DESC, post_event_id);

CREATE INDEX post_events_by_user
ON post_events(user_id, event_kind_id, created_ms DESC, post_event_id)
WHERE user_id IS NOT NULL;

CREATE TABLE post_versions (
    post_version_id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL,
    updater_id INTEGER,
    rating_id INTEGER,
    parent_post_id INTEGER,
    source_updated_ms INTEGER,
    created_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX post_versions_by_post
ON post_versions(post_id, created_ms DESC, post_version_id);

CREATE TABLE post_replacements (
    post_replacement_id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL,
    creator_id INTEGER,
    status_id INTEGER NOT NULL,
    created_ms INTEGER,
    updated_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX post_replacements_by_post
ON post_replacements(post_id, status_id, created_ms DESC, post_replacement_id);

CREATE TABLE post_approvals (
    post_approval_id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL,
    approver_id INTEGER,
    created_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX post_approvals_by_post
ON post_approvals(post_id, created_ms DESC, post_approval_id);

CREATE TABLE queue_payloads (
    queue_payload_id INTEGER PRIMARY KEY,
    payload_json TEXT NOT NULL
) STRICT;

CREATE TABLE queue_jobs (
    queue_job_id INTEGER PRIMARY KEY,
    source_run_id INTEGER,
    kind_id INTEGER NOT NULL,
    state_id INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    available_ms INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    lease_expires_ms INTEGER,
    locked_by TEXT,
    queue_payload_id INTEGER NOT NULL,
    created_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL,
    started_ms INTEGER,
    completed_ms INTEGER,
    last_error TEXT,
    FOREIGN KEY (source_run_id) REFERENCES source_runs(source_run_id) ON DELETE SET NULL,
    FOREIGN KEY (queue_payload_id) REFERENCES queue_payloads(queue_payload_id) ON DELETE RESTRICT,
    CHECK (attempts >= 0),
    CHECK (max_attempts >= 1)
) STRICT;

CREATE INDEX queue_jobs_ready
ON queue_jobs(kind_id, priority DESC, available_ms, queue_job_id)
WHERE state_id = 0;

CREATE INDEX queue_jobs_expired_leases
ON queue_jobs(lease_expires_ms, queue_job_id)
WHERE state_id = 1;

CREATE INDEX queue_jobs_by_source_run
ON queue_jobs(source_run_id, queue_job_id)
WHERE source_run_id IS NOT NULL;

CREATE TABLE queue_job_events (
    queue_job_event_id INTEGER PRIMARY KEY,
    queue_job_id INTEGER NOT NULL,
    event_kind_id INTEGER NOT NULL,
    message TEXT,
    metadata_json TEXT,
    created_ms INTEGER NOT NULL,
    FOREIGN KEY (queue_job_id) REFERENCES queue_jobs(queue_job_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX queue_job_events_by_job
ON queue_job_events(queue_job_id, created_ms, queue_job_event_id);

CREATE TABLE enrichment_coverage (
    scope_id INTEGER NOT NULL,
    coverage_key TEXT NOT NULL,
    dependency_id INTEGER NOT NULL,
    state_id INTEGER NOT NULL,
    enriched_ms INTEGER,
    expires_ms INTEGER,
    source_run_id INTEGER,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_ms INTEGER NOT NULL,
    PRIMARY KEY (scope_id, coverage_key, dependency_id),
    FOREIGN KEY (source_run_id) REFERENCES source_runs(source_run_id) ON DELETE SET NULL,
    CHECK (error_count >= 0)
) STRICT, WITHOUT ROWID;

CREATE INDEX enrichment_coverage_by_dependency
ON enrichment_coverage(dependency_id, state_id, updated_ms);

CREATE TABLE import_runs (
    import_run_id INTEGER PRIMARY KEY,
    source_run_id INTEGER,
    entity_kind_id INTEGER NOT NULL,
    state_id INTEGER NOT NULL,
    started_ms INTEGER NOT NULL,
    completed_ms INTEGER,
    imported_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    FOREIGN KEY (source_run_id) REFERENCES source_runs(source_run_id) ON DELETE SET NULL,
    CHECK (imported_count >= 0),
    CHECK (rejected_count >= 0)
) STRICT;

CREATE TABLE stage_posts (
    import_run_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    rating_id INTEGER NOT NULL,
    source_created_ms INTEGER,
    source_updated_ms INTEGER,
    cached_ms INTEGER NOT NULL,
    file_ext TEXT,
    file_size_bytes INTEGER,
    file_width INTEGER,
    file_height INTEGER,
    file_md5 BLOB,
    score_total INTEGER,
    favorite_count INTEGER,
    comment_count INTEGER,
    uploader_id INTEGER,
    approver_id INTEGER,
    parent_post_id INTEGER,
    child_count INTEGER,
    duration_ms INTEGER,
    flags INTEGER,
    description TEXT,
    sample_url TEXT,
    sample_width INTEGER,
    sample_height INTEGER,
    preview_url TEXT,
    preview_width INTEGER,
    preview_height INTEGER,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (import_run_id, post_id),
    FOREIGN KEY (import_run_id) REFERENCES import_runs(import_run_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE TABLE stage_post_tags (
    import_run_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    tag_name TEXT NOT NULL,
    normalized_tag_name TEXT NOT NULL,
    category_id INTEGER NOT NULL,
    cached_ms INTEGER NOT NULL,
    PRIMARY KEY (import_run_id, normalized_tag_name, post_id),
    FOREIGN KEY (import_run_id) REFERENCES import_runs(import_run_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE INDEX stage_post_tags_by_post
ON stage_post_tags(import_run_id, post_id, normalized_tag_name);

CREATE TABLE stage_post_sources (
    import_run_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    source_hash BLOB NOT NULL,
    source_url TEXT NOT NULL,
    PRIMARY KEY (import_run_id, post_id, source_hash),
    FOREIGN KEY (import_run_id) REFERENCES import_runs(import_run_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE TABLE stage_post_files (
    import_run_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    variant_id INTEGER NOT NULL,
    source_hash BLOB,
    source_url TEXT,
    local_path TEXT,
    file_ext TEXT,
    width INTEGER,
    height INTEGER,
    size_bytes INTEGER,
    md5 BLOB,
    download_state_id INTEGER NOT NULL,
    bytes_written INTEGER,
    checksum BLOB,
    downloaded_ms INTEGER,
    created_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL,
    PRIMARY KEY (import_run_id, post_id, variant_id),
    FOREIGN KEY (import_run_id) REFERENCES import_runs(import_run_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE TABLE import_rejections (
    import_rejection_id INTEGER PRIMARY KEY,
    import_run_id INTEGER NOT NULL,
    entity_kind_id INTEGER NOT NULL,
    entity_id TEXT,
    reason_code INTEGER NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT,
    created_ms INTEGER NOT NULL,
    FOREIGN KEY (import_run_id) REFERENCES import_runs(import_run_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX import_rejections_by_run
ON import_rejections(import_run_id, import_rejection_id);

CREATE TABLE tag_import_unresolved (
    relation_kind TEXT NOT NULL,
    antecedent_name TEXT NOT NULL,
    consequent_name TEXT NOT NULL,
    status_id INTEGER NOT NULL,
    created_ms INTEGER NOT NULL,
    PRIMARY KEY (relation_kind, antecedent_name, consequent_name)
) STRICT, WITHOUT ROWID;
