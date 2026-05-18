CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    category INTEGER NOT NULL,
    post_count INTEGER,
    created_at TEXT,
    updated_at TEXT,
    is_deprecated INTEGER,
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE tag_aliases (
    id INTEGER PRIMARY KEY,
    antecedent_name TEXT NOT NULL,
    consequent_name TEXT NOT NULL,
    antecedent_normalized TEXT NOT NULL,
    consequent_normalized TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT,
    creator_id INTEGER,
    approver_id INTEGER,
    reason TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE tag_implications (
    id INTEGER PRIMARY KEY,
    antecedent_tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    consequent_tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    created_at TEXT,
    updated_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'deleted', 'pending', 'rejected')),
    antecedent_name_snapshot TEXT,
    consequent_name_snapshot TEXT,
    reason TEXT,
    creator_id INTEGER,
    approver_id INTEGER,
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE unresolved_tag_implications (
    id INTEGER PRIMARY KEY,
    antecedent_name_snapshot TEXT,
    consequent_name_snapshot TEXT,
    created_at TEXT,
    updated_at TEXT,
    status TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE tag_implication_closure (
    antecedent_tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    consequent_tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    depth INTEGER NOT NULL CHECK (depth >= 1),
    via_tag_id INTEGER REFERENCES tags(id) ON DELETE SET NULL,
    PRIMARY KEY (antecedent_tag_id, consequent_tag_id)
);

CREATE INDEX idx_tags_name ON tags (name);
CREATE INDEX idx_tags_category ON tags (category);
CREATE INDEX idx_tags_post_count ON tags (post_count);
CREATE INDEX idx_tag_aliases_antecedent ON tag_aliases (antecedent_normalized, status);
CREATE INDEX idx_tag_aliases_consequent ON tag_aliases (consequent_normalized, status);
CREATE INDEX idx_tag_implications_from ON tag_implications (antecedent_tag_id);
CREATE INDEX idx_tag_implications_to ON tag_implications (consequent_tag_id);
CREATE INDEX idx_tag_implications_active_from ON tag_implications (antecedent_tag_id, consequent_tag_id) WHERE status = 'active';
CREATE INDEX idx_tag_implications_status ON tag_implications (status);
CREATE INDEX idx_tag_implication_closure_from ON tag_implication_closure (antecedent_tag_id);
CREATE INDEX idx_tag_implication_closure_to ON tag_implication_closure (consequent_tag_id);
CREATE INDEX idx_tag_implication_closure_depth ON tag_implication_closure (depth);
