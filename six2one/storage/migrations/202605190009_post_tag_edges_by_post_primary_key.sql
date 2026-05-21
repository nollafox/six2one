CREATE TABLE post_tag_edges_new (
    post_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (post_id, tag_id),
    FOREIGN KEY (post_id) REFERENCES posts(post_id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

INSERT OR IGNORE INTO post_tag_edges_new (post_id, tag_id)
SELECT post_id, tag_id
FROM post_tag_edges
ORDER BY post_id, tag_id;

DROP TABLE post_tag_edges;

ALTER TABLE post_tag_edges_new RENAME TO post_tag_edges;

DROP INDEX IF EXISTS post_tag_edges_by_post;
