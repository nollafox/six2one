ALTER TABLE post_files ADD COLUMN source_url TEXT;

UPDATE post_files
SET source_url = (
    SELECT sources.source_url
    FROM sources
    WHERE sources.source_id = post_files.source_id
)
WHERE source_id IS NOT NULL
  AND source_url IS NULL;
