-- These secondary indexes on posts and post_files are fully superseded by the
-- LMDB bitmap and ordered-array index layer, which handles tag/rating/score/
-- favcount/ordering for all production query paths.
--
-- posts_by_rating_created, posts_by_score, posts_by_favorites, posts_by_parent:
--   LMDB covers rating bitmaps, score/favcount ordered arrays, and relation bitmaps.
--   PostQueryBuilder is legacy; production queries go through IndexedPostSearch.
--
-- post_files_by_md5:
--   No query path uses sorted MD5 access; stale-download detection scans by state.
DROP INDEX IF EXISTS posts_by_rating_created;
DROP INDEX IF EXISTS posts_by_score;
DROP INDEX IF EXISTS posts_by_favorites;
DROP INDEX IF EXISTS posts_by_parent;
DROP INDEX IF EXISTS post_files_by_md5;
