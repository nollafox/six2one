CREATE TABLE IF NOT EXISTS post_tag_sets (
    post_id INTEGER PRIMARY KEY,
    tag_ids BLOB NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE
) STRICT;
