"""Endpoint constants for e621 managers."""

POSTS_INDEX = "/posts.json"
POST_SHOW = "/posts/{id}.json"
POST_RANDOM = "/posts/random.json"

COMMENTS_INDEX = "/comments.json"
POST_COMMENTS = "/posts/{id}/comments.json"

NOTES_INDEX = "/notes.json"
NOTE_VERSIONS_INDEX = "/note_versions.json"

POST_FLAGS_INDEX = "/post_flags.json"
POST_EVENTS_INDEX = "/post_events.json"
POST_VERSIONS_INDEX = "/post_versions.json"
POST_APPROVALS_INDEX = "/post_approvals.json"

POOLS_INDEX = "/pools.json"
POOL_SHOW = "/pools/{id}.json"
POOL_VERSIONS_INDEX = "/pool_versions.json"

POST_SETS_INDEX = "/post_sets.json"
POST_SET_SHOW = "/post_sets/{id}.json"

POST_REPLACEMENTS_INDEX = "/post_replacements.json"
POST_REPLACEMENTS_FOR_POST = "/posts/{id}/replacements.json"

FAVORITES_INDEX = "/favorites.json"
POST_FAVORITES = "/posts/{id}/favorites.json"
POST_VOTES_INDEX = "/post_votes.json"

USERS_INDEX = "/users.json"
USER_SHOW = "/users/{id}.json"
USER_ME = "/users/me.json"

ARTISTS_INDEX = "/artists.json"
ARTIST_SHOW = "/artists/{id}.json"
ARTIST_URLS_INDEX = "/artist_urls.json"
ARTIST_VERSIONS_INDEX = "/artist_versions.json"

DB_EXPORT_INDEX = "/db_export/"
DB_EXPORT_FILE = "/db_export/{kind}-{date}.csv.gz"
