CREATE VIRTUAL TABLE IF NOT EXISTS post_descriptions_fts
USING fts5(post_id UNINDEXED, description, tokenize='trigram');

CREATE VIRTUAL TABLE IF NOT EXISTS post_sources_fts
USING fts5(post_id UNINDEXED, source_url, tokenize='trigram');

CREATE VIRTUAL TABLE IF NOT EXISTS post_notes_fts
USING fts5(post_id UNINDEXED, body, tokenize='trigram');
