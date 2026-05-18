ALTER TABLE images RENAME TO images_legacy;

CREATE TABLE images (
    post_id INTEGER NOT NULL,
    variant TEXT NOT NULL,
    source_url TEXT NOT NULL,
    local_path TEXT,
    file_ext TEXT,
    width INTEGER,
    height INTEGER,
    size_bytes INTEGER,
    md5 TEXT,
    state TEXT NOT NULL DEFAULT 'pending',
    bytes_written INTEGER,
    checksum TEXT,
    downloaded_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (post_id, variant),
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);

INSERT INTO images (
    post_id, variant, source_url, local_path, state, bytes_written, checksum,
    downloaded_at, created_at, updated_at
)
SELECT
    post_id,
    'original',
    file_url,
    destination,
    state,
    bytes_written,
    checksum,
    CASE WHEN state = 'downloaded' THEN updated_at ELSE NULL END,
    updated_at,
    updated_at
FROM images_legacy;

DROP TABLE images_legacy;

CREATE INDEX idx_images_post ON images(post_id);
CREATE INDEX idx_images_variant ON images(variant);
