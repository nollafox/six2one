-- posts_by_md5 is fully superseded by the LMDB bitmap/ordered index layer.
-- No SQL query path uses it; dropping it reduces write overhead on every post import.
DROP INDEX IF EXISTS posts_by_md5;
