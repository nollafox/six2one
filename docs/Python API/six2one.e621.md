# six2one.e621

The remote **e621 API client** for six2one.

This owns HTTP, authentication,rate limiting, retries, pagination, and ORM-style access to every e621 resource.

It deliberately knows **nothing** about local storage, query semantics, or
enrichment scheduling. 

---

## Contents

- [six2one.e621](#six2onee621)
  - [Contents](#contents)
  - [Design](#design)
  - [Design boundary](#design-boundary)
  - [Quick start](#quick-start)
  - [The client](#the-client)
  - [Managers](#managers)
    - [Verbs](#verbs)
    - [The full manager list](#the-full-manager-list)
  - [Models](#models)
    - [Materialization rules](#materialization-rules)
    - [The `Post` model](#the-post-model)
      - [Value object shapes](#value-object-shapes)
    - [Every model](#every-model)
  - [Relations](#relations)
    - [Relation map](#relation-map)
  - [Collections](#collections)
    - [Bulk helpers](#bulk-helpers)
    - [prefetch()](#prefetch)
  - [Database exports](#database-exports)
    - [Export methods](#export-methods)
    - [The `Export` object](#the-export-object)
  - [Errors](#errors)
  - [Permission-sensitive resources](#permission-sensitive-resources)
  - [Identity map](#identity-map)
  - [Sync vs async](#sync-vs-async)
  - [Package layout](#package-layout)
  - [Endpoint reference](#endpoint-reference)
  - [Golden example: the entire surface](#golden-example-the-entire-surface)

---

## Design

Four layers, each with one job.

| Layer | Owns |
|---|---|
| `E621Client` | HTTP session, auth, User-Agent, rate limiting, retries, identity map |
| Managers | Endpoint-specific verbs: `get()`, `get_many()`, `search()`, `random()` |
| Models | Typed fields and lazy, cached relations |
| Collections | Pagination, iteration, materialization, `prefetch()`, bulk helpers |

The result is an API with no HTTP grit in the caller's teeth:

```python
post = client.posts.get(6407238)

post.rating          # plain field
post.uploader        # fires GET /users/{id}.json, then cached
post.comments        # returns a lazy Collection; iteration/materialization fetches
post.pools           # lazy Collection hydrated from ids embedded in the post JSON
```

---

## Design boundary

`six2one.e621` is a remote API client. It exposes e621 resources as managers,
models, collections, relations, and database exports.

It does not decide which exports are required, which sidecars should be fetched
for a query, how local enrichment coverage is represented, or how queue jobs are
scheduled.

Those decisions belong to `six2one.bootstrap`, `six2one.query`, `six2one.store`,
and `six2one.queue`. A worked call chain showing how the layers cooperate is in
[What this package does not do](#what-this-package-does-not-do).

---

## Quick start

```python
from six2one.e621 import E621Client

client = E621Client(
    auth=("username", "api_key"),
    user_agent="six2one/0.1 by username",   # required by e621
)

# Fetch a single post.
post = client.posts.get(6407238)
print(post.rating, post.file.url)

# Walk a lazy HasMany relation. The collection fetches as it is iterated, then caches.
for comment in post.comments:
    print(comment.creator.name, comment.body)

# Search returns a lazy collection. No request until you iterate.
posts = client.posts.search("dragon rating:s order:score", limit=320)

# prefetch() avoids N+1 request soup.
for post in posts.prefetch("uploader", "comments"):
    print(post.id, post.uploader.name)

# Download the tag database export.
client.db_exports.tags().download("./cache/")
```

---

## The client

```python
E621Client(
    auth=None,                  # (username, api_key) tuple, or None for anonymous
    user_agent=...,             # REQUIRED; e621 rejects requests without a descriptive UA
    base_url="https://e621.net",
    rate_limit="1/s",           # transport-enforced; callers never think about this
    identity_map=True,          # see "Identity map"
    timeout=30.0,               # per-request seconds
    max_retries=3,              # retried on 429 / 5xx with backoff
)
```

The client exposes one manager per resource (see below) plus:

```python
client.me()                    # -> User, the authenticated viewer (/users/me.json)
client.identity_map             # -> IdentityMap, see below
client.close()                  # close the underlying HTTP session
```

`E621Client` is a context manager:

```python
with E621Client(user_agent="six2one/0.1 by username") as client:
    ...
```

---

## Managers

Managers are thin. They translate verbs into requests and hand back models or
collections. They never decide *what* to fetch — only *how*.

### Verbs

| Verb | Returns | Notes |
|---|---|---|
| `get(id)` | a `Model` | Only on managers with item/show endpoints; raises `E621NotFoundError` if absent |
| `get_many([ids])` | `Collection` | Only on managers with `get()`; batched where the endpoint allows it |
| `search(**kwargs)` | `Collection` | Lazy; see [Collections](#collections) |
| `random(tags="")` | a `Model` | `posts` only |

Search keyword arguments are **typed and clean**. The ugly `search[...]` wire
format is trapped in the basement:

```python
client.comments.search(post_id=12345, creator_name="Bob")
# wire: ?search[post_id]=12345&search[creator_name]=Bob

client.notes.search(post_id=12345, body_matches="hello", is_active=True)
client.pools.search(name_matches="cute_dragons", is_active=True)
```

`client.posts.search()` is the one exception: it takes a **raw e621 tag
string**, because the query language is parsed upstream by `six2one.query`. No
query builder lives here.

```python
client.posts.search("canine order:score rating:s", limit=320, page=2)
```

### The full manager list

| Manager | `search()` keyword arguments |
|---|---|
| `client.posts` | `tags` (raw string), `limit`, `page` |
| `client.comments` | `post_id`, `creator_name`, `creator_id` |
| `client.notes` | `post_id`, `creator_name`, `creator_id`, `body_matches`, `is_active` |
| `client.note_versions` | `post_id`, `updater_id` |
| `client.post_flags` | `post_id`, `creator_name`, `creator_id` |
| `client.post_events` | `post_id`, `creator_id`, `action` |
| `client.post_versions` | `post_id`, `updater_id` |
| `client.deleted_posts` | facade over `posts.search("status:deleted ...")` |
| `client.post_approvals` | `post_id`, `user_id` |
| `client.pools` | `name_matches`, `id`, `creator_name`, `is_active` |
| `client.pool_versions` | `pool_id` |
| `client.sets` | `shortname`, `name`, `creator_name` |
| `client.post_replacements` | `post_id`, `creator_id`, `status` |
| `client.favorites` | `user_id` |
| `client.post_votes` | `post_id`, `user_id` — **Moderator+ / permission-sensitive** |
| `client.users` | `name_matches`, `id` |
| `client.artists` | `name`, `creator_name`, `is_active` |
| `client.artist_urls` | `artist_id`, `url_matches` |
| `client.artist_versions` | `artist_id`, `updater_id` |
| `client.db_exports` | — (see [Database exports](#database-exports)) |

Every `search()` also accepts `limit` and `page`. Not every manager supports every verb: `search()` is the baseline for indexed resources, while `get()`/`get_many()` are exposed only where the API provides an item/show endpoint or a safe equivalent.

A manager should only expose the methods that are meaningful for its endpoint. Unsupported verbs are absent from the manager rather than implemented as runtime stubs. For example, `posts`, `comments`, `notes`, `pools`, `sets`, `users`, and `artists` may expose `get()`, while index-only resources can expose only `search()`.

> **There is no live tag manager.** This package does not expose live tag
> managers for six2one's search semantics. Tag search semantics — names,
> categories, aliases, implications, popularity ordering, transitive closure —
> are sourced from database exports through
> [`client.db_exports`](#database-exports) and imported into `six2one.tags`.
>
> e621 may expose live tag-related routes, but six2one does not use them for
> local query semantics: aliases, implications, popularity, and closure need a
> consistent snapshot, which only a dated export provides.

---

## Models

A model wraps one e621 resource. It carries typed field access, lazy relations,
and a private relation cache.

```python
post = client.posts.get(6407238)

post.id                  # 6407238
post.rating              # "s"
post.score.total         # nested value objects where the JSON nests
post.file.url            # FileInfo: .url .ext .width .height .size .md5
post.file.download("./out/")
post.download("./out/")  # convenience: downloads the full file
```

### Materialization rules

These methods **never** trigger a network request:

```python
post.loaded("comments")        # bool — is the relation already cached?
post.to_dict(expand=False)     # dict of fields only (default)
post.to_json(expand=False)     # JSON string of fields only (default)
repr(post)                     # shows loaded fields only — safe in debuggers/logs
```

These **may fetch**:

```python
post.uploader                  # BelongsTo — fetches immediately if not cached
post.approver                  # BelongsTo — fetches immediately if not cached
post.parent                    # BelongsTo — fetches immediately if not cached
post.load("comments")          # explicit fetch/materialize-if-missing; returns the relation
post.reload("comments")        # discards the cache entry and refetches/materializes
post.refresh()                 # re-GET the resource itself; clears all relations
post.to_dict(expand=["comments", "uploader"])   # fetches missing expanded relations, then dumps
```

Collection relations are lazy on attribute access:

```python
comments = post.comments       # HasMany — returns lazy Collection[Comment], no request yet
comments.all()                 # materializes; fetches pages and may raise API errors
for comment in post.comments:  # materializes page by page
    ...
```

`expand` may be `True` (all relations) or a list of relation names.

### The `Post` model

Every field present in the `/posts.json` payload has a typed accessor. Nothing
requires reaching into `post._data`.

**Scalar fields** — present on the post itself, no fetch:

| Accessor | Type | JSON source |
|---|---|---|
| `post.id` | `int` | `id` |
| `post.created_at` | `datetime` | `created_at` |
| `post.updated_at` | `datetime` | `updated_at` |
| `post.rating` | `str` | `rating` (`"s"`, `"q"`, `"e"`) |
| `post.description` | `str` | `description` |
| `post.sources` | `list[str]` | `sources` |
| `post.fav_count` | `int` | `fav_count` |
| `post.comment_count` | `int` | `comment_count` |
| `post.change_seq` | `int` | `change_seq` |
| `post.duration` | `float \| None` | `duration` (media length; `None` for stills) |
| `post.has_notes` | `bool` | `has_notes` |
| `post.is_favorited` | `bool` | `is_favorited` (relative to the authenticated viewer) |
| `post.locked_tags` | `list[str]` | `locked_tags` |
| `post.uploader_name` | `str` | `uploader_name` |
| `post.uploader_id` | `int` | `uploader_id` |
| `post.approver_id` | `int \| None` | `approver_id` |
| `post.parent_id` | `int \| None` | `relationships.parent_id` |
| `post.child_ids` | `list[int]` | `relationships.children` |
| `post.pool_ids` | `list[int]` | `pools` |
| `post.has_children` | `bool` | `relationships.has_children` |
| `post.has_active_children` | `bool` | `relationships.has_active_children` |

**Nested value objects** — typed wrappers over nested JSON, no fetch:

| Accessor | Type | JSON source |
|---|---|---|
| `post.file` | `FileInfo` | `file` |
| `post.preview` | `ImageVariant` | `preview` |
| `post.sample` | `SampleVariant` | `sample` |
| `post.score` | `Score` | `score` |
| `post.flags` | `Flags` | `flags` |
| `post.tags` | `Tags` | `tags` |

**Relations** — resolved from ids in the payload. `BelongsTo` fetches once, then caches; `EmbeddedIds` returns a lazy collection:

| Accessor | Kind | JSON source |
|---|---|---|
| `post.uploader` | `BelongsTo` → `User` | `uploader_id` |
| `post.approver` | `BelongsTo` → `User \| None` | `approver_id` |
| `post.parent` | `BelongsTo` → `Post \| None` | `relationships.parent_id` |
| `post.children` | `EmbeddedIds` → lazy `Collection[Post]` | `relationships.children` |
| `post.pools` | `EmbeddedIds` → lazy `Collection[Pool]` | `pools` |

That accounts for every key in the payload — `relationships` is flattened across
`post.parent_id`, `post.parent`, `post.child_ids`, `post.children`,
`post.has_children`, and `post.has_active_children`.

#### Value object shapes

```python
post.file        # FileInfo
  .url .ext .width .height .size .md5
  .download(dest)            # save the full media file

post.preview     # ImageVariant
  .url .alt .width .height

post.sample      # SampleVariant
  .url .alt .width .height
  .has                       # bool — whether a sample rendition exists
  .alternates                # dict[str, ImageVariant] — alternate renditions

post.score       # Score
  .up .down .total

post.flags       # Flags
  .deleted .flagged .pending
  .note_locked .rating_locked .status_locked

post.tags        # Tags
  .artist .character .contributor .copyright
  .general .invalid .lore .meta .species   # each -> list[str]
  .all                       # flattened list[str] across all categories
  .categories                # dict[str, list[str]] keyed by category
  "fox" in post.tags         # membership across all categories
  for tag in post.tags: ...  # iterates the flattened list
```

`post.download(dest)` is a convenience equal to `post.file.download(dest)`.

### Every model

```
Post            PostVersion      Pool            PostVote
Comment         DeletedPost      PoolVersion     User
Note            PostApproval     PostSet         Artist
NoteVersion     PostFlag         PostReplacement ArtistUrl
PostEvent       Favorite                         ArtistVersion
```

Raw, unmodelled fields are always reachable through `post._data` as an escape
hatch, but prefer the typed accessors.

---

## Relations

Relations are **descriptors**. The first attribute access resolves the
relation object and caches it on the instance. `BelongsTo` relations fetch the
referenced model immediately. `HasMany` and `EmbeddedIds` relations return lazy
`Collection` objects; those collections fetch only when materialized. Three kinds:

| Descriptor | Resolves to | Mechanism |
|---|---|---|
| `BelongsTo` | a single `Model` | follows a foreign key, e.g. `uploader_id` → `GET /users/{id}` |
| `HasMany` | a lazy `Collection` | a scoped `search()`, e.g. `search[post_id]` |
| `EmbeddedIds` | a lazy `Collection` | hydrates an id array already present in the JSON |

Relations are **not always bidirectional** — they exist only where the public
API can actually answer them. For example, there is no `post.sets`, because
nothing in the post payload reports set membership.

`PostSet.posts` should only hydrate embedded `post_ids` when the payload includes
them. It should not call `/post_sets/{id}/post_list.json`; public set membership
lookup should use `client.sets.search(post_id=...)`.

> `set:` query syntax is still fully supported by higher layers. It is backed by
> the local store's set-post membership index in `six2one.store`, not by a
> `Post.sets` relation.

### Relation map

**`Post`**

| Relation | Kind | Target |
|---|---|---|
| `uploader` | BelongsTo | `User` |
| `approver` | BelongsTo | `User` |
| `parent` | BelongsTo | `Post` |
| `children` | EmbeddedIds | `Post` |
| `pools` | EmbeddedIds | `Pool` |
| `comments` | HasMany | `Comment` |
| `notes` | HasMany | `Note` |
| `note_versions` | HasMany | `NoteVersion` |
| `flag_reports` | HasMany | `PostFlag` |
| `events` | HasMany | `PostEvent` |
| `versions` | HasMany | `PostVersion` |
| `approvals` | HasMany | `PostApproval` |
| `replacements` | HasMany | `PostReplacement` |
| `favorites` | HasMany | `Favorite` — *permission-sensitive* |
| `votes` | HasMany | `PostVote` — *permission-sensitive* |

> `post.flags` is the **status value object** (`deleted`, `pending`, the
> lock flags). The HasMany relation to user-submitted `PostFlag` records is
> named `post.flag_reports` to avoid shadowing it.

**Other models**

| Model | Relations |
|---|---|
| `Comment` | `post` → `Post`, `creator` → `User` |
| `Note` | `post` → `Post`, `creator` → `User` |
| `NoteVersion` | `post` → `Post`, `updater` → `User` |
| `PostFlag` | `post` → `Post`, `creator` → `User` |
| `PostEvent` | `post` → `Post`, `creator` → `User` |
| `PostVersion` | `post` → `Post`, `updater` → `User` |
| `PostApproval` | `post` → `Post`, `user` → `User` |
| `Pool` | `posts` → `Post`, `creator` → `User`, `versions` → `PoolVersion` |
| `PoolVersion` | `pool` → `Pool`, `updater` → `User` |
| `PostSet` | `creator` → `User`; `posts` only when embedded `post_ids` are present |
| `PostReplacement` | `post` → `Post`, `creator` → `User` |
| `Favorite` | `post` → `Post`, `user` → `User` |
| `PostVote` | `post` → `Post`, `user` → `User` |
| `User` | `favorites` → `Favorite` — *permission-sensitive* |
| `Artist` | `urls` → `ArtistUrl`, `versions` → `ArtistVersion`, `creator` → `User` |
| `ArtistUrl` | `artist` → `Artist` |
| `ArtistVersion` | `artist` → `Artist`, `updater` → `User` |

Permission-sensitive relations raise `E621PermissionError` when the protected
resource is fetched or materialized. For lazy collection relations, this usually
happens on `.all()`, `.first()`, iteration, indexing, or another materializing
operation. They never return a misleadingly empty collection to mean denial —
absence and denial are different things.

---

## Collections

`search()`, `get_many()`, and `HasMany` relations all return a `Collection`. A
collection is **lazy and page-backed**: creating it issues no request.

```python
posts = client.posts.search("dragon")   # query object — zero requests
```

Requests fire only when you ask for data:

```python
posts.first()        # fetches one page, returns the first item (or None)
posts.page(2)        # fetches one specific page -> list
posts.all()          # fetches every page -> list (materializes fully)
list(posts)          # same as all()
for post in posts:   # pages fetched on demand as you iterate
    ...
len(posts)           # materializes fully to count
posts[5]             # fetches as many pages as needed to reach index 5
posts.ids()          # list of resource ids
posts.limit(50)      # cap this collection to at most 50 materialized items
```

### Bulk helpers

```python
posts.to_dict()                 # list of dicts
posts.to_json()                 # JSON array string
posts.download_all("./out/")    # Collection[Post] only — downloads every file
```

### prefetch()

`prefetch()` is what makes descriptor relations safe at scale. Without it,
iterating 300 posts and touching `.comments` on each is 300 requests.

```python
posts = client.posts.search("canine").prefetch("comments", "uploader")

for post in posts:
    post.comments    # cache hit — no request
    post.uploader    # cache hit — no request
```

Behaviour contract:

- `prefetch()` returns **the same collection object**, not a new type.
- It does **not** eagerly materialize the full result set. It registers prefetch
  requirements and satisfies them **page by page**: when a page of posts is
  fetched, the relations for *that page* are fetched in batched requests and
  written into each model's relation cache.
- `BelongsTo` targets (like `uploader`) are de-duplicated by id before fetching;
  combined with the identity map, each user is fetched once.

So this stays bounded — it never fetches beyond what you iterate:

```python
for post in posts.prefetch("comments"):
    if done: break          # only the pages you touched were prefetched
```

---

## Database exports

`client.db_exports` exposes the gzipped CSV database exports available from
e621. For six2one, these are the source for tag search semantics — names,
categories, aliases, implications, popularity, closure — because those need a
consistent dated snapshot rather than a moving live view.

The client does not decide which exports an application must consume.
Applications choose which exports to download and how to import them.

```python
client.db_exports.latest_date()      # -> "2026-05-17", newest available export

export = client.db_exports.tags(date=None)   # date defaults to latest
```

### Export methods

| Method | Returns |
|---|---|
| `db_exports.tags(date=None)` | `Export` — tag names, categories, post counts |
| `db_exports.tag_aliases(date=None)` | `Export` — alias canonicalization |
| `db_exports.tag_implications(date=None)` | `Export` — implication edges |
| `db_exports.wiki_pages(date=None)` | `Export` — wiki/search metadata *(optional)* |
| `db_exports.pools(date=None)` | `Export` — pool metadata *(optional)* |
| `db_exports.posts(date=None)` | `Export` — bulk post mirror *(optional)* |
| `db_exports.latest_date()` | `str` |

### The `Export` object

```python
export.date                     # the resolved export date
export.download("./cache/")     # save the .csv.gz file; returns the local Path
export.path                     # local cached Path once downloaded

export.rows()                   # streaming iterator of raw dict rows (decompressed)
export.records()                # streaming iterator of typed export records
```

`records()` yields **export-specific record types**, deliberately separate from
the live API models — CSV export rows and live JSON shapes can drift, and one
class should not have to straddle both:

```
TagExportRecord            WikiPageExportRecord
TagAliasExportRecord       PoolExportRecord
TagImplicationExportRecord PostExportRecord
```

```python
for tag in client.db_exports.tags().records():
    store.tags.upsert(tag)      # six2one.tags converts records into store models
```

Both `rows()` and `records()` stream — they do not load the whole file into
memory.

---

## Errors

All exceptions derive from `E621Error`.

| Exception | Raised when |
|---|---|
| `E621Error` | base class — catch this to catch everything |
| `E621APIError` | unexpected API response; carries `.status_code`, `.response` |
| `E621AuthError` | missing or invalid credentials |
| `E621PermissionError` | authenticated, but not allowed to see this resource |
| `E621RateLimitError` | rate limit exceeded; carries `.retry_after` |
| `E621NotFoundError` | resource does not exist (`get()` on an unknown id) |

```python
from six2one.e621 import E621PermissionError

try:
    post.votes
except E621PermissionError:
    ...   # absence of permission, not absence of data
```

The transport retries `E621RateLimitError` and 5xx responses automatically up to
`max_retries`; the exception only surfaces to callers once retries are
exhausted.

---

## Permission-sensitive resources

Some e621 resources require authentication or elevated permissions —
favorites and post vote rows are the common cases. `GET /post_votes.json` is Moderator+. When the server denies access, the
client raises `E621PermissionError`.

The client **never returns an empty collection to mean "permission denied."**
Absence of data and absence of permission are different facts, and the caller
must be able to tell them apart:

```python
try:
    votes = post.votes.all()      # actual vote data; materialization may raise
except E621PermissionError:
    ...                            # denied — not the same as "no votes"
```

This is API correctness, not application policy. How a higher layer records or
reacts to a permission denial — coverage flags, retries, anything else — is the
business of `six2one.store` and `six2one.queue`, not this package.

---

## Identity map

When `identity_map=True` (the default), the client keeps a
`(resource, id) -> model` cache. Resolving the same resource twice returns the
**same object**:

```python
post.uploader is comment.creator      # True, when both are user 123
```

This deduplicates fetches and keeps memory flat across a session. It is
client-scoped and controllable:

```python
client.identity_map.clear()
client.identity_map.discard("users", 123)
```

Because cached objects can go stale within a long-lived client, use
`model.refresh()` to force a fresh fetch of a specific resource. If you do not
want shared identity at all, construct the client with `identity_map=False`.

---

## Sync vs async

This package is **synchronous**. The relation API depends on it:

```python
post.comments        # a plain attribute access returning a lazy Collection
```

An attribute cannot be a coroutine, so the ergonomic ORM surface commits to sync
by design. Collection relations still fetch only when materialized. If an async client is added later it will be a **separate class**
(`AsyncE621Client`) with an explicit relation API (`await post.comments.load()`)
rather than a contortion of this one.

---

## Package layout

```text
six2one/e621/
  __init__.py        # public exports: E621Client, models, errors
  client.py          # E621Client, identity map
  transport.py       # HTTP session, auth, User-Agent, rate limiting, retries
  managers.py        # per-resource managers and their verbs
  models.py          # Model classes and typed field accessors
  relations.py       # BelongsTo / HasMany / EmbeddedIds descriptors
  collections.py     # Collection: paging, iteration, prefetch, bulk helpers
  exports.py         # db_exports manager, Export, export record types
  errors.py          # E621Error hierarchy
```

---

## Endpoint reference

Every endpoint family this package touches. Exact query parameter names are normalized by managers and transport; callers use the typed manager keyword arguments documented above.

| Endpoint | Used by |
|---|---|
| `GET /posts.json` | `posts.search()` |
| `GET /posts.json?tags=status:deleted` | `deleted_posts.search()`, `deleted_posts.get()` facade |
| `GET /posts/{id}.json` | `posts.get()`, `Post.refresh()` |
| `GET /posts/random.json` | `posts.random()` |
| `GET /posts/{id}/comments.json` | materializing `Post.comments` |
| `GET /posts/{id}/favorites.json` | materializing `Post.favorites` |
| `GET /posts/{id}/replacements.json` | materializing `Post.replacements` |
| `GET /comments.json` | `comments.search()`, prefetch batching |
| `GET /comments/{id}.json` | `comments.get()` |
| `GET /notes.json` | `notes.search()`, materializing `Post.notes` |
| `GET /notes/{id}.json` | `notes.get()` |
| `GET /note_versions.json` | `note_versions.search()`, materializing `Post.note_versions` |
| `GET /post_flags.json` | `post_flags.search()`, materializing `Post.flag_reports` |
| `GET /post_events.json` | `post_events.search()`, materializing `Post.events` |
| `GET /post_versions.json` | `post_versions.search()`, materializing `Post.versions` |
| `GET /post_approvals.json` | `post_approvals.search()`, materializing `Post.approvals` |
| `GET /pools.json` | `pools.search()`, materializing `Post.pools` |
| `GET /pools/{id}.json` | `pools.get()` |
| `GET /pool_versions.json` | `pool_versions.search()`, `Pool.versions` |
| `GET /post_sets.json` | `sets.search()` |
| `GET /post_sets/{id}.json` | `sets.get()` |
| `GET /post_replacements.json` | `post_replacements.search()` |
| `GET /favorites.json` | `favorites.search()`, `User.favorites` |
| `GET /post_votes.json` | `post_votes.search()`, materializing `Post.votes` |
| `GET /users.json` | `users.search()` |
| `GET /users/{id}.json` | `users.get()`, all `BelongsTo` user relations |
| `GET /users/me.json` | `client.me()` |
| `GET /artists.json` | `artists.search()` |
| `GET /artists/{id}.json` | `artists.get()` |
| `GET /artist_urls.json` | `artist_urls.search()`, `Artist.urls` |
| `GET /artist_versions.json` | `artist_versions.search()`, `Artist.versions` |
| `GET /db_export/tags-{date}.csv.gz` | `db_exports.tags()` |
| `GET /db_export/tag_aliases-{date}.csv.gz` | `db_exports.tag_aliases()` |
| `GET /db_export/tag_implications-{date}.csv.gz` | `db_exports.tag_implications()` |
| `GET /db_export/wiki_pages-{date}.csv.gz` | `db_exports.wiki_pages()` |
| `GET /db_export/pools-{date}.csv.gz` | `db_exports.pools()` |
| `GET /db_export/posts-{date}.csv.gz` | `db_exports.posts()` |
| `GET {post.file.url}` | `Post.download()`, `FileInfo.download()`, `Collection.download_all()` |

---

## Golden example: the entire surface

One script that touches every manager, verb, model, value object, relation,
collection operation, export, and error type. It is a guided tour, not a
realistic workflow — no sane program calls everything at once.

```python
"""six2one.e621 — complete API surface tour."""

from six2one.e621 import (
    E621Client,
    E621Error,            # base — catches everything below
    E621APIError,
    E621AuthError,
    E621PermissionError,
    E621RateLimitError,
    E621NotFoundError,
)

# ── 1. Client construction ────────────────────────────────────────────────
# Every constructor argument, shown explicitly. Defaults are noted in "The
# client" section; only user_agent is required.
client = E621Client(
    auth=("username", "api_key"),
    user_agent="six2one/0.1 by username",
    base_url="https://e621.net",
    rate_limit="1/s",
    identity_map=True,
    timeout=30.0,
    max_retries=3,
)
# E621Client is also a context manager (`with E621Client(...) as client:`);
# here we drive it explicitly and call client.close() at the end.

viewer = client.me()                       # GET /users/me.json -> User
print("authenticated as", viewer.name)


# ── 2. Posts manager: get, get_many, random, search ──────────────────────
post   = client.posts.get(6407513)                      # -> Post
trio   = client.posts.get_many([6407513, 6407238, 1])   # -> Collection[Post]
lucky  = client.posts.random("canine rating:s")         # -> Post
hits   = client.posts.search(                           # -> Collection[Post]
    "dragon rating:s order:score", limit=320, page=1,
)   # raw e621 tag string — no query builder lives here


# ── 3. Post scalar fields (no network) ───────────────────────────────────
post.id
post.created_at
post.updated_at
post.rating
post.description
post.sources
post.fav_count
post.comment_count
post.change_seq
post.duration                  # float | None
post.has_notes
post.is_favorited
post.locked_tags
post.uploader_name
post.uploader_id
post.approver_id
post.parent_id
post.child_ids
post.pool_ids
post.has_children
post.has_active_children


# ── 4. Post value objects (no network) ───────────────────────────────────
post.file.url, post.file.ext, post.file.width
post.file.height, post.file.size, post.file.md5
post.preview.url, post.preview.alt, post.preview.width, post.preview.height
post.sample.url, post.sample.alt, post.sample.width, post.sample.height
post.sample.has, post.sample.alternates                 # dict[str, ImageVariant]
post.score.up, post.score.down, post.score.total
post.flags.deleted, post.flags.flagged, post.flags.pending
post.flags.note_locked, post.flags.rating_locked, post.flags.status_locked
post.tags.artist, post.tags.character, post.tags.contributor
post.tags.copyright, post.tags.general, post.tags.invalid
post.tags.lore, post.tags.meta, post.tags.species
post.tags.all                                           # flattened list[str]
post.tags.categories                                    # dict[str, list[str]]
"fox" in post.tags                                       # membership
for tag in post.tags:                                    # iteration
    pass

post.file.download("./out/")                             # save the media file
post.download("./out/")                                  # convenience alias


# ── 5. Post relations ────────────────────────────────────────────────────
# BelongsTo attributes fetch immediately if not cached.
# HasMany / EmbeddedIds attributes return lazy Collections.
post.uploader                  # BelongsTo  -> User
post.approver                  # BelongsTo  -> User | None
post.parent                    # BelongsTo  -> Post | None
post.children                  # EmbeddedIds -> lazy Collection[Post]
post.pools                     # EmbeddedIds -> lazy Collection[Pool]
post.comments                  # HasMany    -> lazy Collection[Comment]
post.notes                     # HasMany    -> lazy Collection[Note]
post.note_versions             # HasMany    -> lazy Collection[NoteVersion]
post.flag_reports              # HasMany    -> lazy Collection[PostFlag]
post.events                    # HasMany    -> lazy Collection[PostEvent]
post.versions                  # HasMany    -> lazy Collection[PostVersion]
post.approvals                 # HasMany    -> lazy Collection[PostApproval]
post.replacements              # HasMany    -> lazy Collection[PostReplacement]
post.favorites                 # HasMany    -> lazy Collection[Favorite]  (perm-sensitive)
post.votes                     # HasMany    -> lazy Collection[PostVote] (perm-sensitive)


# ── 6. Model lifecycle methods ────────────────────────────────────────────
post.loaded("comments")        # bool — no network
post.load("comments")          # materialize/fetch if missing, return relation
post.reload("comments")        # discard cache entry, refetch/rematerialize
post.refresh()                 # re-GET the post itself, clear all relations
post.to_dict()                 # fields only (expand=False default)
post.to_dict(expand=True)      # fetch + include all relations
post.to_json(expand=["uploader", "comments"])            # selective expand
post._data                     # raw payload escape hatch


# ── 7. Every other manager, with typed search kwargs ─────────────────────
client.comments.get(123)
client.comments.search(post_id=6407513, creator_name="Bob", creator_id=846033)
client.notes.search(post_id=6407513, creator_name="Bob", creator_id=846033,
                    body_matches="hello", is_active=True)
client.note_versions.search(post_id=6407513, updater_id=846033)
client.post_flags.search(post_id=6407513, creator_name="Bob", creator_id=846033)
client.post_events.search(post_id=6407513, creator_id=846033, action="deleted")
client.post_versions.search(post_id=6407513, updater_id=846033)
client.deleted_posts.search(limit=1)
client.deleted_posts.get(6407513)
client.post_approvals.search(post_id=6407513, user_id=846033)
client.pools.get(45000)
client.pools.search(name_matches="cute_dragons", id=45000,
                    creator_name="Bob", is_active=True)
client.pool_versions.search(pool_id=45000)
client.sets.get(8888)
client.sets.search(shortname="my_set", name="My Set", creator_name="Bob")
client.post_replacements.search(post_id=6407513, creator_id=846033,
                                status="pending")
client.favorites.search(user_id=846033)                  # permission-sensitive
client.post_votes.search(post_id=6407513, user_id=846033) # Moderator+ / permission-sensitive
client.posts.search("votedup:me", limit=1)                 # viewer-safe vote discovery
client.users.get(846033)
client.users.search(name_matches="TheKonig*", id=846033)
client.artists.get(1234)
client.artists.search(name="kwoutj", creator_name="Bob", is_active=True)
client.artist_urls.search(artist_id=1234, url_matches="*x.com*")
client.artist_versions.search(artist_id=1234, updater_id=846033)


# ── 8. Relations on every non-post model ─────────────────────────────────
comment = client.comments.get(123)
comment.post; comment.creator

note = client.notes.search(post_id=6407513).first()
note.post; note.creator

nver = client.note_versions.search(post_id=6407513).first()
nver.post; nver.updater

flag = client.post_flags.search(post_id=6407513).first()
flag.post; flag.creator

event = client.post_events.search(post_id=6407513).first()
event.post; event.creator

pver = client.post_versions.search(post_id=6407513).first()
pver.post; pver.updater

deleted = client.deleted_posts.search(limit=1)
client.deleted_posts.get(6407513).first()
deleted.post

approval = client.post_approvals.search(post_id=6407513).first()
approval.post; approval.user

pool = client.pools.get(45000)
pool.posts; pool.creator; pool.versions

plver = client.pool_versions.search(pool_id=45000).first()
plver.pool; plver.updater

pset = client.sets.get(8888)
pset.post_ids                  # if present in payload
pset.posts                     # embedded IDs only
pset.creator

repl = client.post_replacements.search(post_id=6407513).first()
repl.post; repl.creator

fav = client.favorites.search(user_id=846033).first()
fav.post; fav.user

vote = client.post_votes.search(post_id=6407513).first()
vote.post; vote.user

user = client.users.get(846033)
user.favorites                 # HasMany -> lazy Collection[Favorite] (perm-sensitive)

artist = client.artists.get(1234)
artist.urls; artist.versions; artist.creator

aurl = client.artist_urls.search(artist_id=1234).first()
aurl.artist

aver = client.artist_versions.search(artist_id=1234).first()
aver.artist; aver.updater


# ── 9. Collections: laziness, materialization, bulk, prefetch ────────────
results = client.posts.search("canine")     # query object — zero requests
results.first()                             # one page -> Post | None
results.page(2)                             # one page -> list[Post]
results.all()                               # every page -> list[Post]
results.ids()                               # list[int]
list(results)                               # == all()
len(results)                                # materializes to count
results[5]                                  # indexes, fetching pages as needed
results.limit(50)                           # cap the result set
for post in results:                        # pages fetched on demand
    pass
results.to_dict()                           # list[dict]
results.to_json()                           # JSON array string
results.download_all("./out/")              # Collection[Post] only

# prefetch() returns the SAME collection; relations are batched page by page.
for post in client.posts.search("dragon").prefetch("uploader", "comments"):
    post.uploader                           # cache hit — no request
    post.comments                           # cache hit — no request


# ── 10. Database exports ─────────────────────────────────────────────────
client.db_exports.latest_date()                          # -> "2026-05-17"

for export in (
    client.db_exports.tags(),                # date defaults to latest
    client.db_exports.tag_aliases(),
    client.db_exports.tag_implications(),
    client.db_exports.wiki_pages(),          # optional
    client.db_exports.pools(),               # optional
    client.db_exports.posts(date="2026-05-16"),   # optional, explicit date
):
    export.date                              # resolved export date
    export.download("./cache/")              # save the .csv.gz -> Path
    export.path                              # local cached Path
    for row in export.rows():                # raw dict rows, streaming
        break
    for record in export.records():          # typed export records, streaming
        break


# ── 11. Error handling: every exception type ─────────────────────────────
try:
    client.posts.get(999_999_999)
except E621NotFoundError:
    pass                                     # no such resource

try:
    post.votes.all()
except E621PermissionError:
    pass                                     # denied — not "no data"

try:
    client.posts.search("anything").all()
except E621AuthError:
    pass                                     # bad / missing credentials
except E621RateLimitError as exc:
    print("retry after", exc.retry_after)    # only after retries exhausted
except E621APIError as exc:
    print("api error", exc.status_code)      # unexpected response
except E621Error:
    pass                                     # catch-all base class


# ── 12. Identity map and teardown ────────────────────────────────────────
post.uploader is comment.creator             # True when both are the same user
client.identity_map.discard("users", 846033)
client.identity_map.clear()
client.close()
```