# TODO: Large-Scale Data Features for six2one

six2one works well for small to mid-sized datasets today, but the JSON backend starts to strain under larger archives. For tag analytics, dataset exploration, and machine-learning workflows, six2one needs stronger storage primitives, better indexing, resumable caching, and a query layer that preserves e621 search semantics without forcing users to write backend-specific queries.

## Value Statement

six2one should grow into a storage-aware, queryable dataset toolkit that:

1. Provides an elegant, discoverable API and module layout.
   1. Beginners should be able to contribute, use, and extend it without spelunking through implementation details.
   2. Core classes should be understandable by name, responsibility, and composition.

2. Supports robust, well-indexed storage backends.
   1. Results should be retrieved from local disk when available.
   2. Missing results should be fetchable through the same query interface, regardless of query complexity.
   3. SQLite should provide a fast path for large datasets, tag analytics, and indexed search.

3. Provides e621-compliant query semantics across backends.
   1. The query layer should support native e621 search syntax.
   2. Local backends should maintain e621 tag aliases and implications where possible.
   3. Users should not need to write SQL when a well-known query language already exists.
   4. Backend adapters should be easy to add, replace, or compose.
      - Example: a read-through cache could compose `WebBackendAdapter` and `SqliteBackendAdapter`.

4. Delivers a polished CLI and UX experience.
   1. Errors should be human-readable, color-coded, and written as full sentences.
   2. Errors should suggest follow-up commands when applicable.
   3. Long-running operations should report phase, progress counts, and estimated time when reliable.

5. Handles resumability and cache integrity intelligently.
   1. Data should be validated before being written to cache or storage.
   2. Query discovery should determine post/page counts where possible.
   3. Cached query results should include timestamps and source metadata.
   4. Caches should auto-invalidate after one month by default, or be manually invalidated by the user.
   5. Uses a queuing system beneath the hood. 
      * `621 fetch [query]` automatically fills and drains the queue
      * `621 queue [query]` takes same options as fetch, but simply adds to the queue.
      * `621 fetch --queue` drains the queue, and checks if any items are left after completion, if

6. Is configurable and inspectable.
   1. Configuration and cached files should live under `~/.six2one` by default.
   2. `621 config image.storage_method=[compressed|raw]`
   3. `621 config api.username=[USERNAME] api.token=[TOKEN]`
   4. `621 config backend.default=[json|sqlite]`


# 0) Implement `query` package for six2one

`six2one.query` will have the following file directory structure:

```
src/six2one/query/
  __init__.py
  language.py      # public API
  parser.py        # internal-ish
  binder.py        # internal-ish
  registry.py
  ast.py           # Refer to AST.md for specific classes
  diagnostics.py
```

The public interface will look something like this:
```python

language = E621QueryLanguage(registries=registries)

compiled = language.compile("dragon rating:s score:>1 order:score")
bound = compiled.bound
```


In future tasks, this will enable adapters to use it like so:
```python
class QueryableAdapter(Backend):
    query_language: E621QueryLanguage

    def search(self, query: str) -> SearchResult:
        compiled = self.query_language.compile(query)

        if not compiled.ok:
            return SearchResult(
                posts=[],
                diagnostics=compiled.diagnostics,
            )

        return self.find(compiled.bound)
```

And backend adapters will own the planning of the language:

```python
class JsonAdapter(QueryableAdapter):
    def find(self, query: BoundQuery) -> SearchResult:
        plan = self.json_planner.plan(query)
        return self.json_executor.execute(plan)


class SqliteAdapter(QueryableAdapter):
    def find(self, query: BoundQuery) -> SearchResult:
        plan = self.sqlite_planner.plan(query)
        return self.sqlite_executor.execute(plan)

```


Inside `language.py` there is a class named `E621QueryLanguage`






# 1) Add support for multiple backends.

By default six2one uses json as the backend. This is ok for small-to-medium size projects, but starts to suffer when we're indexing tens-to-hundreds of thousands of images.

We'd like to add a few commands for selecting the backend, and converting one backend to another:

```bash
# Configures global backend for new outdirs, writes a config file next to `six2one login` 
$ 621 backend use json
$ 621 backend use sqlite

# Converts a given outdir from one directory to another.
$ 621 backend convert [directory] --json
$ 621 backend convert [directory] --sqlite
```

The main difference between these is where and how posts are stored. When posts are stored

In the source code, we should create a new src/six2one/backends/* directory like the following after the AST is built:

```
src/six2one/backends/
  data_adapter.py
  queryable_adapter.py
  six2one.py

  json/
    __init__.py
    adapter.py
    planner.py
    executor.py
    indexes.py

  sqlite/
    __init__.py
    adapter.py
    planner.py
    executor.py
    schema.py
    migrations.py
```



The data-adapter.py should have a `Backend` class child classes to override:
1. `get(id)`                  — returns original post json as dictionary. Raises NotImplemented.
2. `update(id, payload)`      — updates post in backend with payload. Raises NotImplemented.
3. `create(id, payload)`      — creates post in backend with payload. Raises NotImplemented.
4. `delete(id)`               — deletes a post in backend with id. Raises NotImplemented.
5. `find(query: QueryObject)` — Efficiently searches the backend and returns a collection of posts matching kwargs semantics. Raises NotImplemented.

Note that for sqlite, these will likely have to update / fetch numerous tables. We should be indexing in a way that makes search as efficient as possible, according to the e621 docs on searching semantics. Each data adapter class will take care of performing `find` efficiently. 

The queryable-adapter.py has a `QueryableAdapter`, which only adds a single public method `search`, which child classes should not override. 
1. `search(query: str)` — allows searching the backend with e621 native syntax. Takes care of using the Query object to parse  `self.find` into a `QueryObject`

json.py, sqlite.py, and six2one.py all inherit from `QueryableAdapter`. This ensures that all domain specific logic around `get`, `update`, `create`, `delete` and `find` are handled in a single file by domain. However, before we can do this, we must build a query parser. More on that in the next section.

For example:
```python
class Backend(ABC):
    @abstractmethod
    def find(self, query: BoundQuery) -> SearchResult:
        """
        Execute a semantically bound e621 query using this backend's own planner.
        """
        raise NotImplementedError
```


Then `QueryableAdapter.search()` becomes beautifully small:
```python
class QueryableAdapter(Backend):
    def search(self, query: str) -> SearchResult:
        raw = parse_query(query)
        bound = bind_query(
            raw,
            profile=self.compatibility_profile,
            registries=self.registries,
            capabilities=self.capabilities,
        )
        return self.find(bound)
```



Ideally, in the end, we land at a high-level flow like the following:


The public flow:
```python
backend = open_backend(path)

backend.get(6407238)
backend.search("dragon rating:s score:>1 order:score")
backend.find(bound_query)
```

And this would orchestrate an internal flow like the following:
```
search(str)
  -> parse_query(str)
  -> bind_query(raw, registries, profile)
  -> self.find(bound_query)

find(BoundQuery)
  -> backend-specific planner
  -> backend-specific executor
  -> SearchResult
```













# 2) Build parser for e621 query language.

In order to support searching the backend using identical syntax as e621, we'll want to build a query parser that supports ALL of e621's query language as covered here.

This means that it should respect e621's:

1. Basic Syntax
2. Implication / Chains Semantics 
3. Advanced Group Syntax
4. Metatags
5. Sorting 
6. User-Based Metatags 
7. Post-Based Metatags 
8. Other Metatags
9. Dates
10. Text Searching
11. Range Syntax 
12. Quirks and addendems.

Yes. Here is the **complete compiler-grade table** for the e621-style post search language based on the cheatsheet you attached. Scope note: this is complete against the attached cheatsheet. It does **not** include the separate blacklist-metatag page referenced by the cheatsheet, because that separate list was not included here, and the live e621 help page was not retrievable from this environment. 

## 1. Global Search Rules

| Rule                        | Exact behavior                                                               | Parser / backend requirement                                                              |
| --------------------------- | ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Search target               | Syntax searches **posts**, not tags directly.                                | Query engine evaluates post records plus auxiliary indexes.                               |
| Default logical operator    | Space-separated terms are implicit `AND`.                                    | `cat dog` means `HAS(cat) AND HAS(dog)`.                                                  |
| Term limit                  | Searches allow up to **40 tags/metatags** total.                             | Count normal tags and metatags. Decide whether groups count by contained terms.           |
| Metatags count toward limit | Metatags are included in the 40-term search limit.                           | `rating:s score:>10 cat` counts as 3.                                                     |
| Default ordering            | Default is newest-first by post ID, described as equivalent to `order:id` in e621 docs; exact alias normalization should be confirmed against live e621 behavior.   | Apply sort stage even if query has no explicit `order:`.                                  |
| Deleted posts               | Most searches implicitly remove deleted posts.                               | Inject implicit `status != deleted` unless disabled by status/deletion-related terms.     |
| Blacklist caveat            | Search metatags should **not** be assumed to work identically on blacklists. | Cheatsheet explicitly points to a separate blacklist metatag page, so treat blacklist behavior as a separate compatibility profile.                              |
| Quoted metatag values       | All metatags may accept double-quoted values.                                | `status:"deleted"` parses as `status:deleted`; useful mainly for text values with spaces. |
| Metatag vs meta tag         | “Metatag” means dynamic metadata pseudo-tag, not normal `tags.meta`.         | Keep parser-level metatags distinct from the post’s `tags.meta` array.                    |

## 2. Basic Tag Syntax

| Feature                      | Forms                           | Semantics                                                                                                                              | Caveats / compatibility notes                                                        |
| ---------------------------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Required tag                 | `cat`                           | Post must contain tag.                                                                                                                 | Search should use canonical/aliased tag identity.                                    |
| Multiple required tags       | `cat dog`                       | Post must contain both tags.                                                                                                           | Top-level terms are `AND`.                                                           |
| Multi-word tags              | `red_panda`, `african_wild_dog` | Underscores represent word separators inside tag names.                                                                                | Do not split on `_`.                                                                 |
| Negated tag                  | `-chicken`                      | Post must not contain tag.                                                                                                             | With implication chains, excluding an implying tag also excludes tags that imply it. |
| Include plus exclude         | `fox -chicken`                  | Must contain `fox`; must not contain `chicken`.                                                                                        | Compile as `HAS(fox) AND NOT HAS(chicken)`.                                          |
| Loose OR tag                 | `~cat ~dog`                     | Post may contain either tag or both.                                                                                                   | Does not combine cleanly with positive wildcards.                                    |
| Positive wildcard            | `african_*`                     | Match posts with any tag starting with `african_`.                                                                                     | Limit **one positive wildcard per search**. Results may be incomplete.               |
| Negated wildcard             | `-african_*`                    | Match posts with no tag starting with `african_`.                                                                                      | No limit on number of negated wildcards.                                             |
| Wildcard expansion model     | `*_cat`                         | Positive wildcards are internally expanded into the 40 most popular matching tags and placed in the same loose-OR bucket as `~tag`.    | Do not model as a clean grouped OR unless intentionally deviating.                   |
| Wildcard plus loose OR quirk | `~eagle ~domestic_dog *_cat`    | Behaves like one flat OR pool: `~eagle ~domestic_dog ~domestic_cat ~calico_cat ...`, not `~eagle ~domestic_dog ( ~domestic_cat ... )`. | This is a major exact parser compatibility goblin; preserve the exact flattening behavior.                                          |
| Tilde wildcard quirk         | `~*_cat ~tiger`                 | The wildcard is not resolved. It is added directly as a tag-like item, effectively useless because real tags cannot contain `*`.       | This quirk does **not** apply to negated wildcards.                                  |

## 3. Groups and Boolean Structure

| Feature                      | Forms                                            | Semantics                                                   | Caveats / compatibility notes                                             |
| ---------------------------- | ------------------------------------------------ | ----------------------------------------------------------- | ------------------------------------------------------------------------- |
| Group                        | `( ~cat ~tiger ~leopard )`                       | Parenthesized expression evaluated as a unit.               | Opening `(` must be followed by a space; closing `)` must follow a space. |
| Multiple groups              | `( ~cat ~tiger ) ( ~dog ~wolf )`                 | Both groups must match.                                     | Top-level `AND` still applies.                                            |
| Group OR terms               | `( ~cat ~tiger ~leopard )`                       | At least one of the loose-OR terms in the group must match. | OR is expressed through `~` terms.                                        |
| Prefix on group: negation    | `-( cat dog )`                                   | Post must not satisfy the group expression.                 | Equivalent to `NOT(group)`.                                               |
| Prefix on group: loose OR    | `~( felid -leopard )`                            | Group becomes a loose-OR candidate.                         | Used with other `~` groups or tags.                                       |
| Mixed prefixed groups        | `~( felid -leopard ) ~( leopard tiger )`         | Finds posts satisfying either group.                        | Example: felids without leopard, or leopard plus tiger.                   |
| Nested groups                | `( ~( felid -leopard ) ~( leopard tiger ) ) dog` | Nested expression must match, then `dog` must also match.   | Compile into real AST, not string surgery.                                |
| Nesting limit                | Any nested groups                                | Maximum nesting depth is **10**.                            | Parser should reject, warn, or compatibility-fail past 10.                |
| Parentheses inside tag names | Character tags may contain parentheses.          | `(cat)` is not necessarily a group.                         | Space rule prevents confusion: groups need `( cat )`.                     |

## 4. Aliases and Implications

| Feature                     | Example                                                | Semantics                                                                                                        | Backend requirement                                  |
| --------------------------- | ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| Tag aliases                 | `cat → domestic_cat`                                   | Query terms should resolve to canonical tags.                                                                    | Need alias table/snapshot.                           |
| Direct implications         | `tiger` implies `felid`                                | Searching implied tag can match posts tagged with more specific tags.                                            | Need implication graph or pre-expanded tag closure.  |
| Implication chains          | `hyper_breasts → huge_breasts → big_breasts → breasts` | A post with a high-specificity tag also has all implied ancestors.                                               | Compute transitive closure.                          |
| Positive implied search     | `breasts`                                              | Includes posts with `breasts`, `big_breasts`, `huge_breasts`, `hyper_breasts`, etc.                              | Match against closure, not raw tags only.            |
| Negative implying exclusion | `breasts -huge_breasts`                                | Includes `breasts` and `big_breasts`, but excludes `huge_breasts` and tags implying it, such as `hyper_breasts`. | Negative tag must exclude descendant/implying chain. |
| Alias/implication source    | Tag wiki / tag metasearch data                         | Exact e621 behavior depends on current alias and implication data.                                               | Must snapshot or sync alias/implication graph.       |

## 5. Sorting and Result Controls

| Feature               | Forms                                                      | Semantics                                                                                 | Caveats                                                                           |
| --------------------- | ---------------------------------------------------------- | ----------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Limit                 | `limit:10`                                                 | Sets number of posts per page/result page.                                                | Sorting still follows selected/default order.                                     |
| Random order          | `order:random`                                             | Randomly orders posts.                                                                    | Not deterministic by itself.                                                      |
| Deterministic random  | `randseed:123`                                             | Same seed returns same random result ordering and supports pagination without duplicates. | Seed must be numeric.                                                             |
| Hot order             | `order:hot`                                                | Uses Hot-page ordering.                                                                   | Not reversible.                                                                   |
| Hot window start      | `hot_from:<date>`                                          | Changes start of the 2-day window used by `order:hot`.                                    | Date accepts the same supported date formats as `date:`.                          |
| Reversed order prefix | `-order:score`                                             | Reverses sortable order.                                                                  | `-order:score` equals `order:score_asc`; `-order:score_asc` equals `order:score`. |
| Non-reversible orders | `order:random`, `-order:random`, `order:hot`, `-order:hot` | Negation does not create a true reverse.                                                  | Equivalent to non-negated behavior.                                               |
| Example queries       | `votedup:me order:random limit:1`                           | Example searches illustrate combined syntax rather than separate feature support.        | Do not treat examples like distinct metatag or sort syntax.                        |

## 6. Complete `order:` Alias Matrix

| Sort category         | Main ordering aliases                                                                                                                                                   | Reversed ordering aliases                                                                                                                                                 | Sort key                        |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| Creation date         | `order:created_at`, `order:created_at_desc`, `-order:created_at_asc`, `order:created`, `order:created_desc`, `-order:created_asc`                                       | `-order:created_at`, `-order:created_at_desc`, `order:created_at_asc`, `-order:created`, `-order:created_desc`, `order:created_asc`                                       | `created_at`                    |
| Update date           | `order:updated_at`, `order:updated_at_desc`, `-order:updated_at_asc`, `order:updated`, `order:updated_desc`, `-order:updated_asc`                                       | `-order:updated_at`, `-order:updated_at_desc`, `order:updated_at_asc`, `-order:updated`, `-order:updated_desc`, `order:updated_asc`                                       | `updated_at`                    |
| Comment date          | `order:comm`, `order:comm_desc`, `-order:comm_asc`, `order:comment`, `order:comment_desc`, `-order:comment_asc`                                                         | `-order:comm`, `-order:comm_desc`, `order:comm_asc`, `-order:comment`, `-order:comment_desc`, `order:comment_asc`                                                         | newest/oldest comment timestamp |
| Comment bumped        | `order:comm_bumped`, `order:comm_bumped_desc`, `-order:comm_bumped_asc`, `order:comment_bumped`, `order:comment_bumped_desc`, `-order:comment_bumped_asc`               | `-order:comm_bumped`, `-order:comm_bumped_desc`, `order:comm_bumped_asc`, `-order:comment_bumped`, `-order:comment_bumped_desc`, `order:comment_bumped_asc`               | bumped comment timestamp        |
| Comment count         | `order:comm_count`, `order:comm_count_desc`, `-order:comm_count_asc`, `order:comment_count`, `order:comment_count_desc`, `-order:comment_count_asc`                     | `-order:comm_count`, `-order:comm_count_desc`, `order:comm_count_asc`, `-order:comment_count`, `-order:comment_count_desc`, `order:comment_count_asc`                     | `comment_count`                 |
| Filesize              | `order:size`, `order:size_desc`, `-order:size_asc`, `order:filesize`, `order:filesize_desc`, `-order:filesize_asc`                                                      | `-order:size`, `-order:size_desc`, `order:size_asc`, `-order:filesize`, `-order:filesize_desc`, `order:filesize_asc`                                                      | `file.size`                     |
| Aspect ratio          | `order:ratio`, `order:ratio_desc`, `-order:ratio_asc`, `order:aspect_ratio`, `order:aspect_ratio_desc`, `-order:aspect_ratio_asc`, `-order:portrait`, `order:landscape` | `-order:ratio`, `-order:ratio_desc`, `order:ratio_asc`, `-order:aspect_ratio`, `-order:aspect_ratio_desc`, `order:aspect_ratio_asc`, `order:portrait`, `-order:landscape` | `file.width / file.height`      |
| Resolution            | `order:mpixels`, `order:mpixels_desc`, `-order:mpixels_asc`                                                                                                             | `-order:mpixels`, `-order:mpixels_desc`, `order:mpixels_asc`                                                                                                              | megapixels                      |
| General tag count     | `order:general_tags`, `order:general_tags_desc`, `-order:general_tags_asc`, `order:gentags`, `order:gentags_desc`, `-order:gentags_asc`                                 | `-order:general_tags`, `-order:general_tags_desc`, `order:general_tags_asc`, `-order:gentags`, `-order:gentags_desc`, `order:gentags_asc`                                 | `tags.general.length`           |
| Artist tag count      | `order:artist_tags`, `order:artist_tags_desc`, `-order:artist_tags_asc`, `order:arttags`, `order:arttags_desc`, `-order:arttags_asc`                                    | `-order:artist_tags`, `-order:artist_tags_desc`, `order:artist_tags_asc`, `-order:arttags`, `-order:arttags_desc`, `order:arttags_asc`                                    | `tags.artist.length`            |
| Contributor tag count | `order:contributor_tags`, `order:contributor_tags_desc`, `-order:contributor_tags_asc`, `order:conttags`, `order:conttags_desc`, `-order:conttags_asc`                  | `-order:contributor_tags`, `-order:contributor_tags_desc`, `order:contributor_tags_asc`, `-order:conttags`, `-order:conttags_desc`, `order:conttags_asc`                  | `tags.contributor.length`       |
| Copyright tag count   | `order:copyright_tags`, `order:copyright_tags_desc`, `-order:copyright_tags_asc`, `order:copytags`, `order:copytags_desc`, `-order:copytags_asc`                        | `-order:copyright_tags`, `-order:copyright_tags_desc`, `order:copyright_tags_asc`, `-order:copytags`, `-order:copytags_desc`, `order:copytags_asc`                        | `tags.copyright.length`         |
| Character tag count   | `order:character_tags`, `order:character_tags_desc`, `-order:character_tags_asc`, `order:chartags`, `order:chartags_desc`, `-order:chartags_asc`                        | `-order:character_tags`, `-order:character_tags_desc`, `order:character_tags_asc`, `-order:chartags`, `-order:chartags_desc`, `order:chartags_asc`                        | `tags.character.length`         |
| Species tag count     | `order:species_tags`, `order:species_tags_desc`, `-order:species_tags_asc`, `order:spectags`, `order:spectags_desc`, `-order:spectags_asc`                              | `-order:species_tags`, `-order:species_tags_desc`, `order:species_tags_asc`, `-order:spectags`, `-order:spectags_desc`, `order:spectags_asc`                              | `tags.species.length`           |
| Invalid tag count     | `order:invalid_tags`, `order:invalid_tags_desc`, `-order:invalid_tags_asc`, `order:invtags`, `order:invtags_desc`, `-order:invtags_asc`                                 | `-order:invalid_tags`, `-order:invalid_tags_desc`, `order:invalid_tags_asc`, `-order:invtags`, `-order:invtags_desc`, `order:invtags_asc`                                 | `tags.invalid.length`           |
| Meta tag count        | `order:meta_tags`, `order:meta_tags_desc`, `-order:meta_tags_asc`, `order:metatags`, `order:metatags_desc`, `-order:metatags_asc`                                       | `-order:meta_tags`, `-order:meta_tags_desc`, `order:meta_tags_asc`, `-order:metatags`, `-order:metatags_desc`, `order:metatags_asc`                                       | `tags.meta.length`              |
| Lore tag count        | `order:lore_tags`, `order:lore_tags_desc`, `-order:lore_tags_asc`, `order:lortags`, `order:lortags_desc`, `-order:lortags_asc`                                          | `-order:lore_tags`, `-order:lore_tags_desc`, `order:lore_tags_asc`, `-order:lortags`, `-order:lortags_desc`, `order:lortags_asc`                                          | `tags.lore.length`              |
| ID                    | `order:id`, `-order:id_desc`, `order:id_asc`                                                                                                                            | `-order:id`, `order:id_desc`, `-order:id_asc` — preserve e621’s documented aliases and verify live behavior.                                                                                                                             | `id`                            |
| Score                 | `order:score`, `order:score_desc`, `-order:score_asc`                                                                                                                   | `-order:score`, `-order:score_desc`, `order:score_asc`                                                                                                                    | `score.total`                   |
| MD5                   | `order:md5`, `order:md5_desc`, `-order:md5_asc`                                                                                                                         | `-order:md5`, `-order:md5_desc`, `order:md5_asc`                                                                                                                          | `file.md5`                      |
| Favorite count        | `order:favcount`, `order:favcount_desc`, `-order:favcount_asc`                                                                                                          | `-order:favcount`, `-order:favcount_desc`, `order:favcount_asc`                                                                                                           | `fav_count`                     |
| Note date             | `order:note`, `order:note_desc`, `-order:note_asc`                                                                                                                      | `-order:note`, `-order:note_desc`, `order:note_asc`                                                                                                                       | newest/oldest note timestamp    |
| Total tag count       | `order:tagcount`, `order:tagcount_desc`, `-order:tagcount_asc`                                                                                                          | `-order:tagcount`, `-order:tagcount_desc`, `order:tagcount_asc`                                                                                                           | total tags                      |
| Change sequence       | `order:change`, `order:change_desc`, `-order:change_asc`                                                                                                                | `-order:change`, `-order:change_desc`, `order:change_asc`                                                                                                                 | `change_seq`                    |
| Duration              | `order:duration`, `order:duration_desc`, `-order:duration_asc`                                                                                                          | `-order:duration`, `-order:duration_desc`, `order:duration_asc`                                                                                                           | `duration`                      |
| Random                | `order:random`, `-order:random`                                                                                                                                         | No reversed ordering supported                                                                                                                                            | random                          |
| Hot                   | `order:hot`, `-order:hot`                                                                                                                                               | No reversed ordering supported                                                                                                                                            | hot score/window                |

## 7. User-Based Metatags

| Feature                     | Forms / aliases                           | Semantics                           | Caveats / data required                                            |
| --------------------------- | ----------------------------------------- | ----------------------------------- | ------------------------------------------------------------------ |
| Uploader by name            | `user:Bob`                                | Posts uploaded by user named Bob.   | Needs uploader name.                                               |
| Uploader by ID              | `user:!17633`                             | Posts uploaded by user ID `17633`.  | `!` ID syntax works for user-style metatags.                       |
| Uploader by ID special form | `user_id:17633`                           | Posts uploaded by user ID `17633`.  | Cannot use `user_id:!17633`.                                       |
| Favorited by                | `fav:Bob`, `favoritedby:Bob`              | Posts favorited by Bob.             | Hidden favorites only work for the owner; requires favorites data. |
| Voted by current user       | `voted:anything`                          | Posts the logged-in user voted on.  | Value text is ignored-ish; requires viewer vote data.              |
| Upvoted by current user     | `votedup:anything`, `upvote:anything`     | Posts the logged-in user upvoted.   | Only works while logged in.                                        |
| Downvoted by current user   | `voteddown:anything`, `downvote:anything` | Posts the logged-in user downvoted. | Only staff can search another user’s votes.                        |
| Approved by                 | `approver:Bob`                            | Posts approved by Bob.              | Needs approver user/name data.                                     |
| Deleted by                  | `deletedby:Bob`                           | Posts deleted by Bob.               | Also disables implicit deleted-post filtering.                     |
| Commented by                | `commenter:Bob`, `comm:Bob`               | Posts commented on by Bob.          | Requires comments index.                                           |
| Note written by             | `noter:Bob`                               | Posts with notes written by Bob.    | Requires notes index.                                              |
| Note updated by             | `noteupdater:Bob`                         | Posts with notes updated by Bob.    | Requires notes update metadata.                                    |

## 8. Count and Numeric Post Metatags

All entries in this table support **range syntax**.

| Feature               | Forms               | Semantics                                       | JSON field / derived value |
| --------------------- | ------------------- | ----------------------------------------------- | -------------------------- |
| Post ID               | `id:100`            | Post with ID exactly/range/list matching value. | `id`                       |
| Score                 | `score:100`         | Posts with score matching value.                | `score.total`              |
| Favorite count        | `favcount:100`      | Posts with favorite count matching value.       | `fav_count`                |
| Comment count         | `comment_count:100` | Posts with comment count matching value.        | `comment_count`            |
| Total tag count       | `tagcount:2`        | Posts with total number of tags matching value. | Sum of all tag arrays      |
| General tag count     | `gentags:2`         | Posts with N general tags.                      | `tags.general.length`      |
| Artist tag count      | `arttags:2`         | Posts with N artist tags.                       | `tags.artist.length`       |
| Contributor tag count | `conttags:2`        | Posts with N contributor tags.                  | `tags.contributor.length`  |
| Copyright tag count   | `copytags:2`        | Posts with N copyright tags.                    | `tags.copyright.length`    |
| Character tag count   | `chartags:2`        | Posts with N character tags.                    | `tags.character.length`    |
| Species tag count     | `spectags:2`        | Posts with N species tags.                      | `tags.species.length`      |
| Invalid tag count     | `invtags:2`         | Posts with N invalid tags.                      | `tags.invalid.length`      |
| Meta tag count        | `metatags:2`        | Posts with N normal meta tags.                  | `tags.meta.length`         |
| Lore tag count        | `lortags:2`         | Posts with N lore tags.                         | `tags.lore.length`         |

## 9. Rating Metatags

| Feature      | Forms                             | Semantics                 | JSON field      |
| ------------ | --------------------------------- | ------------------------- | --------------- |
| Safe         | `rating:safe`, `rating:s`         | Posts rated safe.         | `rating == "s"` |
| Questionable | `rating:questionable`, `rating:q` | Posts rated questionable. | `rating == "q"` |
| Explicit     | `rating:explicit`, `rating:e`     | Posts rated explicit.     | `rating == "e"` |

## 10. File Type Metatags

| Feature | Form        | Semantics                          | JSON field |
| ------- | ----------- | ---------------------------------- | ---------- |
| JPEG    | `type:jpg`  | JPG image posts.                   | `file.ext` |
| PNG     | `type:png`  | PNG image posts, may be animated.  | `file.ext` |
| GIF     | `type:gif`  | GIF image posts, often animated.   | `file.ext` |
| WebP    | `type:webp` | WebP image posts, may be animated. | `file.ext` |
| MP4     | `type:mp4`  | MP4 video posts.                   | `file.ext` |
| SWF     | `type:swf`  | Flash posts.                       | `file.ext` |
| WebM    | `type:webm` | WebM video posts.                  | `file.ext` |

## 11. Image and File Size Metatags

| Feature        | Forms                        | Semantics                       | Caveats / implementation                         |
| -------------- | ---------------------------- | ------------------------------- | ------------------------------------------------ |
| Width          | `width:100`, `width:>1000`   | Match file width.               | Supports range syntax.                           |
| Height         | `height:100`, `height:<2000` | Match file height.              | Supports range syntax.                           |
| Megapixels     | `mpixels:1`                  | Match image area in megapixels. | `1000 × 1000 = 1 mpixel`; supports range syntax. |
| Ratio pair     | `ratio:4:3`                  | Match aspect ratio.             | Convert to decimal.                              |
| Ratio decimal  | `ratio:1.33`                 | Match aspect ratio decimal.     | Ratios are rounded to two digits.                |
| Filesize KB    | `filesize:200KB`             | Match file size around 200 KB.  | Exact filesize includes ±5% tolerance.           |
| Filesize MB    | `filesize:2MB`               | Match file size around 2 MB.    | Exact filesize includes ±5% tolerance.           |
| Filesize range | `filesize:200KB..300KB`      | Match files between sizes.      | Parse units before range comparison.             |

## 12. Status Metatags

| Feature                    | Forms                         | Semantics                                | Caveats                                                                |
| -------------------------- | ----------------------------- | ---------------------------------------- | ---------------------------------------------------------------------- |
| Pending                    | `status:pending`              | Posts waiting to be approved or deleted. | Usually `flags.pending == true`.                                       |
| Active                     | `status:active`               | Approved posts.                          | Disables implicit deleted filtering.                                   |
| Deleted                    | `status:deleted`              | Deleted posts.                           | Disables implicit deleted filtering.                                   |
| Flagged                    | `status:flagged`              | Posts flagged for deletion.              | Usually `flags.flagged == true`.                                       |
| Modqueue                   | `status:modqueue`             | Pending or flagged posts.                | `pending OR flagged`.                                                  |
| Any/all                    | `status:any`, `status:all`    | Active or deleted posts.                 | Disables implicit deleted filtering.                                   |
| Explicit deleted exclusion | `-status:deleted`             | Removes deleted posts explicitly.        | Also disables implicit filtering first, then applies explicit removal. |
| Tilde unsupported          | `~status:deleted`             | Not supported.                           | `status:` does not support `~`.                                        |
| Status count per group     | One `status:` per root/group. | Each group has its own one-status slot.  | Nested groups each get one additional status slot.                     |

## 13. Date Metatags

All `date:` forms support range syntax where noted.

| Feature              | Forms                    | Semantics                                 | Caveats                                                                            |
| -------------------- | ------------------------ | ----------------------------------------- | ---------------------------------------------------------------------------------- |
| Absolute ISO date    | `date:2012-04-27`        | Posts uploaded on that date.              | Assumes year-month-day by default.                                                 |
| Absolute named date  | `date:april/27/2012`     | Posts uploaded on that date.              | Needs named-month parser.                                                          |
| Today                | `date:today`             | Posts uploaded today.                     | Depends on chosen timezone boundary.                                               |
| Yesterday            | `date:yesterday`         | Posts uploaded yesterday.                 | Depends on chosen timezone boundary.                                               |
| Last day             | `date:day`               | Posts from the last day.                  | Supports range, ago, yester combinations.                                          |
| Last week            | `date:week`              | Posts from the last 7 days.               | Supports range, ago, yester combinations.                                          |
| Last month           | `date:month`             | Posts from the last 30 days.              | Supports range, ago, yester combinations.                                          |
| Last year            | `date:year`              | Posts from the last 365 days.             | Supports range, ago, yester combinations.                                          |
| Last decade          | `date:decade`            | Posts from the last decade.               | Cheatsheet wording is inconsistent: it says only `x..decade` style is supported but also shows `date:decade..year`; preserve exact e621 compatibility and validate against live behavior. |
| N days ago           | `date:5_days_ago`        | Posts from within the last 5 days.        | Supports range.                                                                    |
| N weeks ago          | `date:5_weeks_ago`       | Posts from within the last 5 weeks.       | Supports range and yester.                                                         |
| N months ago         | `date:5_months_ago`      | Posts from within the last 5 months.      | Supports range and yester.                                                         |
| N years ago          | `date:5_years_ago`       | Posts from within the last 5 years.       | Supports range and yester.                                                         |
| Previous week        | `date:yesterweek`        | Posts from last week.                     | Supports ago composition.                                                          |
| Previous month       | `date:yestermonth`       | Posts from last month.                    | Supports ago composition.                                                          |
| Previous year        | `date:yesteryear`        | Posts from last year.                     | Supports ago composition.                                                          |
| N previous years ago | `date:5_yesteryears_ago` | Posts from 5 years ago.                   | Distinct from “within last 5 years.”                                               |
| Relative date range  | `date:year..month`       | Posts between 30 days ago and 1 year ago. | If relative term is first in range, it starts at the first day of that range.      |
| Hot start date       | `hot_from:<date>`        | Alters `order:hot` 2-day window.          | Use same accepted date parser.                                                     |

## 14. Text Search Metatags

| Feature                  | Forms                       | Semantics                                                                     | Caveats / data required                                                              |
| ------------------------ | --------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Source contains          | `source:*example.com`       | Posts with source containing/matching `example.com`; use wildcards as needed. | Search across `sources[]`.                                                           |
| No source                | `source:none`               | Posts without source.                                                         | Equivalent-ish to `hassource:false`.                                                 |
| Description contains     | `description:whatever`      | Description contains text.                                                    | Should support case-insensitive contains unless matching e621 internals differently. |
| Description phrase       | `description:"hello there"` | Description contains text with spaces.                                        | Requires quoted value lexing.                                                        |
| Note contains            | `note:whatever`             | Notes contain text.                                                           | Requires note data beyond base post JSON.                                            |
| Note phrase              | `note:"hello there"`        | Notes contain phrase.                                                         | Requires note index.                                                                 |
| Deletion reason contains | `delreason:*whatever`       | Deleted posts with deletion reason matching text/wildcard.                    | Requires deletion metadata. Also disables implicit deleted filtering.                |
| Deletion phrase          | `delreason:"bad reason"`    | Deleted posts with deletion reason phrase.                                    | Requires quoted value lexing and deletion metadata.                                  |

## 15. Parent and Child Metatags

| Feature   | Forms           | Opposite / alias | Semantics                       | JSON mapping                                      |
| --------- | --------------- | ---------------- | ------------------------------- | ------------------------------------------------- |
| Is child  | `ischild:true`  | `ischild:false`  | Has a parent / has no parent.   | `relationships.parent_id != null`                 |
| Is parent | `isparent:true` | `isparent:false` | Has children / has no children. | `relationships.has_children` or `children.length` |
| Parent ID | `parent:1234`   | N/A              | Parent ID equals `1234`.        | `relationships.parent_id == 1234`                 |
| No parent | `parent:none`   | `parent:any`     | No parent / any parent.         | Same as `ischild:false` / `ischild:true`          |
| No child  | `child:none`    | `child:any`      | No child / any child.           | Same as `isparent:false` / `isparent:true`        |

## 16. Lock Metatags

| Lock kind     | Positive forms                                   | Negative forms                                      | Loose-OR form    | JSON mapping          |
| ------------- | ------------------------------------------------ | --------------------------------------------------- | ---------------- | --------------------- |
| Rating locked | `ratinglocked:true`, `locked:rating`             | `ratinglocked:false`, `-locked:rating`              | `~locked:rating` | `flags.rating_locked` |
| Note locked   | `notelocked:true`, `locked:note`, `locked:notes` | `notelocked:false`, `-locked:note`, `-locked:notes` | `~locked:note`   | `flags.note_locked`   |
| Status locked | `statuslocked:true`, `locked:status`             | `statuslocked:false`, `-locked:status`              | `~locked:status` | `flags.status_locked` |

## 17. Other Metatags

| Feature              | Forms                                                     | Semantics                                  | Caveats / data required                                       |
| -------------------- | --------------------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------- |
| Has source           | `hassource:true`, `hassource:false`                       | Posts with or without source.              | Map to `sources.length > 0`.                                  |
| Has description      | `hasdescription:true`, `hasdescription:false`             | Posts with or without description.         | Map to non-empty `description`.                               |
| In pool              | `inpool:true`, `inpool:false`                             | Posts that are or are not in a pool.       | Map to `pools.length > 0`.                                    |
| Pending replacements | `pending_replacements:true`, `pending_replacements:false` | Posts with/without pending replacements.   | Requires replacement metadata not present in sample post.     |
| Artist verified      | `artverified:true`, `artverified:false`                   | Posts uploaded by verified artists or not. | Requires artist/user verification data.                       |
| Pool by ID           | `pool:4`                                                  | Posts in pool ID `4`.                      | Sample post has pool IDs only.                                |
| Pool by name         | `pool:fox_and_the_grapes`                                 | Posts in named pool.                       | Requires pool-name lookup.                                    |
| Set by ID            | `set:17`                                                  | Posts in set ID `17`.                      | Requires set membership data.                                 |
| Set by short name    | `set:cute_rabbits`                                        | Posts in set short name.                   | Requires set lookup/membership data.                          |
| MD5                  | `md5:02dd0...`                                            | Exact MD5 hash match.                      | MD5 is unique per image.                                      |
| Duration             | `duration:>120`                                           | Videos with duration at least 120 seconds. | Supports range syntax; still images may have `duration:null`. |

## 18. Range Syntax

| Syntax                  | Equivalent form         | Semantics                            | Applies to                                      |
| ----------------------- | ----------------------- | ------------------------------------ | ----------------------------------------------- |
| Exact                   | `id:100`                | Field equals exactly `100`.          | Range-enabled metatags.                         |
| List                    | `id:100,121,144,...`    | Field equals any listed value.       | Range-enabled metatags.                         |
| Closed range            | `score:25..50`          | Inclusive range from 25 to 50.       | Numeric/date/size fields where supported.       |
| Lower bounded           | `score:>=100`           | At least 100.                        | Equivalent to `score:100..`.                    |
| Lower bounded range     | `score:100..`           | At least 100.                        | Equivalent to `score:>=100`.                    |
| Strict lower            | `score:>100`            | Greater than 100.                    | Equivalent to negating `<=100`.                 |
| Strict lower equivalent | `-score:<=100`          | Greater than 100.                    | Use care with negated range normalization.      |
| Upper bounded           | `favcount:<=100`        | 100 or less.                         | Equivalent to `favcount:..100`.                 |
| Upper bounded range     | `favcount:..100`        | 100 or less.                         | Equivalent to `favcount:<=100`.                 |
| Strict upper            | `favcount:<100`         | Less than 100.                       | Equivalent to negating `>=100`.                 |
| Strict upper equivalent | `-favcount:>=100`       | Less than 100.                       | Use care with negated range normalization.      |
| Date range              | `date:year..month`      | Between 30 days ago and 1 year ago.  | Relative endpoints need special interpretation. |
| Filesize range          | `filesize:200KB..300KB` | File size between 200 KB and 300 KB. | Parse units before comparison.                  |

## 19. Supported Range-Enabled Fields

| Field family          | Metatags                                                                                                                     |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| ID and post counters  | `id:`, `score:`, `favcount:`, `comment_count:`                                                                               |
| Tag counts            | `tagcount:`, `gentags:`, `arttags:`, `conttags:`, `copytags:`, `chartags:`, `spectags:`, `invtags:`, `metatags:`, `lortags:` |
| Image/file dimensions | `width:`, `height:`, `mpixels:`, `ratio:`, `filesize:`                                                                       |
| Dates                 | `date:`                                                                                                                      |
| Duration              | `duration:`                                                                                                                  |

## 20. Compatibility Edge Cases Checklist

| Edge case                      | Required behavior                                                                                                                                       |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Parentheses spacing            | `( cat )` is a group; `(cat)` may be a tag-like token, especially relevant for character tags.                                                          |
| Group nesting                  | Reject or compatibility-fail above 10 nested levels.                                                                                                    |
| Search term limit              | Enforce 40 tags/metatags.                                                                                                                               |
| Positive wildcard limit        | Enforce one positive wildcard per search.                                                                                                               |
| Negated wildcard limit         | No limit for negated wildcards.                                                                                                                         |
| Positive wildcard completeness | Do not promise complete wildcard results if matching e621 exactly.                                                                                      |
| Wildcard + `~`                 | Flatten positive wildcard expansions into the same loose-OR internal bucket instead of nested OR grouping.                                                                                |
| `~*_cat`                       | Do not resolve wildcard when wildcard term itself is prefixed with `~`; it becomes effectively useless.                                                 |
| Metatag wildcard distinction   | Wildcard quirks described for tag wildcards do not necessarily apply to metatag wildcards.                                                              |
| Status `~` unsupported         | `~status:...` must not behave as a valid loose-OR status metatag.                                                                                       |
| One status per group           | Enforce one `status:` in root and one per group scope.                                                                                                  |
| Deleted default filter         | Apply implicit deleted exclusion unless disabled.                                                                                                       |
| Deleted filter disabling       | `status:active`, `status:deleted`, `status:any`, `status:all`, `-status:deleted`, `deletedby:*`, and `delreason:*` disable implicit deletion filtering. |
| `-status:deleted` behavior     | Disables implicit filtering, then explicitly removes deleted posts.                                                                                     |
| Sort negation                  | Normalize `-order:x` to reverse ordering where supported.                                                                                               |
| Non-reversible sort negation   | `-order:random` and `-order:hot` behave like their non-negated forms.                                                                                   |
| `randseed` validation          | Seeds must be numeric.                                                                                                                                  |
| `randseed` pagination          | Same seed should support deterministic pagination without duplicates.                                                                                   |
| `hot_from`                     | Only meaningful with `order:hot`.                                                                                                                       |
| File size exact                | Exact `filesize:` queries include ±5% tolerance.                                                                                                        |
| Ratio precision                | `ratio:` comparison rounds to two decimal places.                                                                                                       |
| Date relative ranges           | Relative first endpoint starts at first day of that range.                                                                                              |
| `date:decade` limitation       | Only specific range composition is supported according to the cheatsheet note.                                                                          |
| User ID syntax                 | `user:!17633` is valid; `user_id:17633` is valid; `user_id:!17633` is not.                                                                              |
| Vote privacy                   | Vote metatags normally only work for logged-in/current user; staff exception for other users.                                                           |
| Favorite privacy               | Other users’ hidden favorites cannot be searched.                                                                                                       |
| Auxiliary data                 | Notes, comments, sets, named pools, favorites, votes, deletions, replacements, and verified artist status require data outside the base post JSON.      |
| Quoted values                  | All metatags can accept quoted values, even if only text metatags truly need them.                                                                      |
| Blacklist mismatch             | Search metatags and blacklist metatags are not interchangeable.                                                                                         |

## 21. Minimal AST Coverage

Typescript-ish shape:

```typescript
/* ============================================================================
 * e621 Search AST
 *
 * Goal:
 *   Represent the full e621-style search language in a phase-aware way:
 *
 *   1. RawQuery / CST:
 *      Preserves exact user input, tokens, prefixes, quotes, source spans,
 *      malformed syntax, and compatibility-sensitive quirks.
 *
 *   2. SemanticQuery:
 *      Normalizes tags, aliases, implications, metatags, ranges, scopes,
 *      deleted-post behavior, and query options.
 *
 *   3. QueryPlan:
 *      Represents the final backend-ready plan for JSON or SQLite evaluation,
 *      including injected predicates, sort options, limits, data dependencies,
 *      diagnostics, and required indexes.
 * ========================================================================== */

type Query = {
  raw: RawQuery;
  semantic: SemanticQuery;
  plan: QueryPlan;
  diagnostics: Diagnostic[];
};

/* ============================================================================
 * Shared Metadata
 * ========================================================================== */

type SourceSpan = {
  start: number;
  end: number;
  text: string;
};

type RawToken = {
  kind:
    | "word"
    | "quoted-string"
    | "open-paren"
    | "close-paren"
    | "prefix"
    | "colon"
    | "range-separator"
    | "comma"
    | "whitespace"
    | "unknown";

  value: string;
  span: SourceSpan;
};

type RawNode = {
  kind:
    | "RawQuery"
    | "RawTerm"
    | "RawGroup"
    | "RawMetatag"
    | "RawQuotedValue"
    | "RawInvalid";

  tokens: RawToken[];
  span: SourceSpan;
};

type Prefix = "none" | "not" | "looseOr";

type DiagnosticSeverity = "info" | "warning" | "error";

type DiagnosticCode =
  | "UNKNOWN_METATAG"
  | "INVALID_METATAG_VALUE"
  | "INVALID_RANGE"
  | "INVALID_DATE"
  | "INVALID_SIZE"
  | "INVALID_RATIO"
  | "INVALID_BOOLEAN"
  | "INVALID_USER_REF"
  | "INVALID_ORDER"
  | "INVALID_LIMIT"
  | "INVALID_RANDSEED"
  | "INVALID_HOT_FROM"
  | "GROUP_DEPTH_EXCEEDED"
  | "GROUP_SPACING_INVALID"
  | "UNCLOSED_GROUP"
  | "UNEXPECTED_CLOSE_GROUP"
  | "TERM_LIMIT_EXCEEDED"
  | "POSITIVE_WILDCARD_LIMIT_EXCEEDED"
  | "STATUS_TILDE_UNSUPPORTED"
  | "STATUS_SCOPE_CONFLICT"
  | "WILDCARD_TILDE_NOT_EXPANDED"
  | "WILDCARD_RESULTS_TRUNCATED"
  | "IMPLICIT_DELETED_FILTER_SUPPRESSED"
  | "AUXILIARY_DATA_REQUIRED"
  | "BACKEND_UNSUPPORTED_FEATURE"
  | "PERMISSION_GATED_FEATURE"
  | "BLACKLIST_PROFILE_UNSUPPORTED"
  | "COMPATIBILITY_AMBIGUITY";

type Diagnostic = {
  severity: DiagnosticSeverity;
  code: DiagnosticCode;
  message: string;
  span?: SourceSpan;
  relatedSpans?: SourceSpan[];
};

/* ============================================================================
 * Phase 1: Raw Query / CST
 * ========================================================================== */

type RawQuery = {
  source: string;
  tokens: RawToken[];
  terms: RawTerm[];
  root: RawNode;
};

type RawTerm =
  | RawTagTerm
  | RawWildcardTerm
  | RawMetatagTerm
  | RawGroupTerm
  | RawInvalidTerm;

type RawTagTerm = {
  kind: "RawTagTerm";
  prefix: Prefix;
  rawName: string;
  span: SourceSpan;
};

type RawWildcardTerm = {
  kind: "RawWildcardTerm";
  prefix: Prefix;
  rawPattern: string;
  span: SourceSpan;
};

type RawMetatagTerm = {
  kind: "RawMetatagTerm";
  prefix: Prefix;
  rawKey: string;
  rawValue: string;
  quoted: boolean;
  span: SourceSpan;
  keySpan: SourceSpan;
  valueSpan: SourceSpan;
};

type RawGroupTerm = {
  kind: "RawGroupTerm";
  prefix: Prefix;
  terms: RawTerm[];
  depth: number;
  hasRequiredSpacing: boolean;
  span: SourceSpan;
};

type RawInvalidTerm = {
  kind: "RawInvalidTerm";
  prefix?: Prefix;
  reason: string;
  span: SourceSpan;
};

/* ============================================================================
 * Phase 2: Semantic Query
 * ========================================================================== */

type SemanticQuery = {
  filter: Expr;
  options: QueryOptions;
  compat: CompatibilityState;
  dataDependencies: DataDependency[];
  diagnostics: Diagnostic[];
};

/* ============================================================================
 * Boolean Expressions
 * ========================================================================== */

type Expr =
  | AllExpr
  | AnyExpr
  | LooseOrBucketExpr
  | NotExpr
  | RootScopeExpr
  | GroupScopeExpr
  | Atom;

type AllExpr = {
  kind: "All";
  terms: Expr[];
  span?: SourceSpan;
};

type AnyExpr = {
  kind: "Any";
  terms: Expr[];
  span?: SourceSpan;
};

/**
 * e621-specific OR bucket.
 *
 * This is intentionally separate from AnyExpr because positive wildcard
 * expansions and ~tag terms share a compatibility-sensitive flattened bucket.
 */
type LooseOrBucketExpr = {
  kind: "LooseOrBucket";
  terms: Expr[];
  source: "tilde" | "wildcard-expansion" | "mixed";
  flattenedWildcardExpansion: boolean;
  span?: SourceSpan;
};

type NotExpr = {
  kind: "Not";
  term: Expr;
  span?: SourceSpan;
};

type RootScopeExpr = {
  kind: "RootScope";
  scopeId: ScopeId;
  term: Expr;
  statusSlot?: StatusTerm;
  diagnostics: Diagnostic[];
  span?: SourceSpan;
};

type GroupScopeExpr = {
  kind: "GroupScope";
  scopeId: ScopeId;
  depth: number;
  term: Expr;
  statusSlot?: StatusTerm;
  diagnostics: Diagnostic[];
  span?: SourceSpan;
};

type ScopeId = string;

/* ============================================================================
 * Atom Nodes
 * ========================================================================== */

type Atom =
  | TagTerm
  | TagWildcard
  | ResolvedTagExpansion

  | StatusTerm
  | RatingTerm
  | FileTypeTerm
  | NumericFieldTerm
  | TagCountTerm
  | DimensionTerm
  | RatioTerm
  | FileSizeTerm
  | DateTerm
  | TextSearchTerm
  | UserTerm
  | ViewerStateTerm
  | RelationshipTerm
  | LockTerm
  | PresenceTerm
  | CollectionTerm
  | HashTerm
  | DurationTerm

  | OptionTerm
  | UnknownMetatagTerm
  | InvalidTerm
  | AuxiliaryPredicate;

/* ============================================================================
 * Tag Terms, Aliases, Implications, Wildcards
 * ========================================================================== */

type TagCategory =
  | "general"
  | "artist"
  | "contributor"
  | "copyright"
  | "character"
  | "species"
  | "invalid"
  | "meta"
  | "lore";

type TagTerm = {
  kind: "TagTerm";
  raw: string;
  canonical?: string;
  category?: TagCategory;
  resolution: TagResolution;
  span: SourceSpan;
};

type TagResolution = {
  aliasApplied: boolean;
  aliasFrom?: string;
  aliasTo?: string;

  /**
   * Tags implied by this tag.
   * Example: hyper_breasts implies huge_breasts, big_breasts, breasts.
   */
  impliedAncestors: string[];

  /**
   * Tags that imply this tag.
   * Needed for negative implication exclusion.
   */
  implyingDescendants: string[];

  matchMode: "raw" | "canonical" | "closure";
};

type TagWildcard = {
  kind: "TagWildcard";
  raw: string;
  pattern: string;
  polarity: "positive" | "negative";

  /**
   * True for syntax like ~*_cat.
   * e621 does not resolve this as a wildcard expansion.
   */
  wasLooseOrPrefixed: boolean;

  expansionPolicy:
    | "expand-positive-top-40"
    | "match-negative-pattern"
    | "do-not-expand-tilde-wildcard";

  expansion?: ResolvedTagExpansion;
  span: SourceSpan;
};

type ResolvedTagExpansion = {
  kind: "ResolvedTagExpansion";
  sourcePattern: string;
  terms: TagTerm[];
  maxTerms: 40;
  truncated: boolean;
  popularityOrdered: boolean;
  span?: SourceSpan;
};

/* ============================================================================
 * Metatag Terms
 * ========================================================================== */

type StatusValue =
  | "pending"
  | "active"
  | "deleted"
  | "flagged"
  | "modqueue"
  | "any"
  | "all";

type StatusTerm = {
  kind: "StatusTerm";
  value: StatusValue;
  negated: boolean;
  scopeId: ScopeId;
  disablesImplicitDeletedFilter: boolean;
  span: SourceSpan;
};

type RatingValue = "s" | "q" | "e";

type RatingTerm = {
  kind: "RatingTerm";
  value: RatingValue;
  rawValue: string;
  span: SourceSpan;
};

type FileTypeValue =
  | "jpg"
  | "png"
  | "gif"
  | "webp"
  | "mp4"
  | "swf"
  | "webm";

type FileTypeTerm = {
  kind: "FileTypeTerm";
  value: FileTypeValue;
  span: SourceSpan;
};

type NumericField =
  | "id"
  | "score"
  | "favcount"
  | "comment_count";

type NumericFieldTerm = {
  kind: "NumericFieldTerm";
  field: NumericField;
  value: NumericValue;
  span: SourceSpan;
};

type TagCountField =
  | "tagcount"
  | "gentags"
  | "arttags"
  | "conttags"
  | "copytags"
  | "chartags"
  | "spectags"
  | "invtags"
  | "metatags"
  | "lortags";

type TagCountTerm = {
  kind: "TagCountTerm";
  field: TagCountField;
  category?: TagCategory;
  value: NumericValue;
  span: SourceSpan;
};

type DimensionField = "width" | "height" | "mpixels";

type DimensionTerm = {
  kind: "DimensionTerm";
  field: DimensionField;
  value: NumericValue;
  span: SourceSpan;
};

type RatioTerm = {
  kind: "RatioTerm";
  value: RatioValue | NumericValue;
  rounding: 2;
  span: SourceSpan;
};

type FileSizeTerm = {
  kind: "FileSizeTerm";
  value: SizeValue | SizeRangeValue;
  exactTolerance?: 0.05;
  span: SourceSpan;
};

type DateTerm = {
  kind: "DateTerm";
  field: "created_at";
  value: DateValue | DateRangeValue;
  clock: DateEvaluationContext;
  span: SourceSpan;
};

type TextSearchField =
  | "source"
  | "description"
  | "note"
  | "delreason";

type TextSearchTerm = {
  kind: "TextSearchTerm";
  field: TextSearchField;
  pattern: TextPattern;
  disablesImplicitDeletedFilter: boolean;
  requiresAuxiliaryData: boolean;
  span: SourceSpan;
};

type UserMetatag =
  | "user"
  | "user_id"
  | "fav"
  | "favoritedby"
  | "approver"
  | "deletedby"
  | "commenter"
  | "comm"
  | "noter"
  | "noteupdater";

type UserTerm = {
  kind: "UserTerm";
  metatag: UserMetatag;
  user: UserRef;
  disablesImplicitDeletedFilter: boolean;
  requiresAuxiliaryData: boolean;
  permission?: PermissionRequirement;
  span: SourceSpan;
};

type ViewerStateMetatag =
  | "voted"
  | "votedup"
  | "upvote"
  | "voteddown"
  | "downvote";

type ViewerStateTerm = {
  kind: "ViewerStateTerm";
  metatag: ViewerStateMetatag;
  state: "voted" | "upvoted" | "downvoted";
  viewerRequired: true;
  permission?: PermissionRequirement;
  span: SourceSpan;
};

type RelationshipTerm =
  | {
      kind: "RelationshipTerm";
      relation: "ischild";
      value: BooleanValue;
      span: SourceSpan;
    }
  | {
      kind: "RelationshipTerm";
      relation: "isparent";
      value: BooleanValue;
      span: SourceSpan;
    }
  | {
      kind: "RelationshipTerm";
      relation: "parent";
      value: IdValue | "none" | "any";
      span: SourceSpan;
    }
  | {
      kind: "RelationshipTerm";
      relation: "child";
      value: "none" | "any";
      span: SourceSpan;
    };

type LockKind = "rating" | "note" | "notes" | "status";

type LockTerm = {
  kind: "LockTerm";
  lock: LockKind;
  value: BooleanValue;
  rawMetatag:
    | "ratinglocked"
    | "notelocked"
    | "statuslocked"
    | "locked";
  span: SourceSpan;
};

type PresenceTerm = {
  kind: "PresenceTerm";
  field:
    | "hassource"
    | "hasdescription"
    | "inpool"
    | "pending_replacements"
    | "artverified";

  value: BooleanValue;
  requiresAuxiliaryData: boolean;
  span: SourceSpan;
};

type CollectionTerm =
  | {
      kind: "CollectionTerm";
      collection: "pool";
      ref: CollectionRef;
      span: SourceSpan;
    }
  | {
      kind: "CollectionTerm";
      collection: "set";
      ref: CollectionRef;
      span: SourceSpan;
    };

type HashTerm = {
  kind: "HashTerm";
  algorithm: "md5";
  value: string;
  span: SourceSpan;
};

type DurationTerm = {
  kind: "DurationTerm";
  value: NumericValue;
  nullPolicy: NullPolicy;
  span: SourceSpan;
};

type AuxiliaryPredicate = {
  kind: "AuxiliaryPredicate";
  name: string;
  value?: unknown;
  dependencies: DataDependency[];
  span?: SourceSpan;
};

type UnknownMetatagTerm = {
  kind: "UnknownMetatagTerm";
  rawKey: string;
  rawValue: string;
  span: SourceSpan;
};

type InvalidTerm = {
  kind: "InvalidTerm";
  reason: string;
  span: SourceSpan;
};

/* ============================================================================
 * Query Option Terms
 *
 * These parse like metatags but are lifted out of the filter expression.
 * ========================================================================== */

type OptionTerm =
  | OrderOptionTerm
  | LimitOptionTerm
  | RandSeedOptionTerm
  | HotFromOptionTerm;

type OrderOptionTerm = {
  kind: "OrderOptionTerm";
  spec: OrderSpec;
  span: SourceSpan;
};

type LimitOptionTerm = {
  kind: "LimitOptionTerm";
  spec: LimitSpec;
  span: SourceSpan;
};

type RandSeedOptionTerm = {
  kind: "RandSeedOptionTerm";
  spec: RandSeedSpec;
  span: SourceSpan;
};

type HotFromOptionTerm = {
  kind: "HotFromOptionTerm";
  spec: HotFromSpec;
  span: SourceSpan;
};

/* ============================================================================
 * Values
 * ========================================================================== */

type NumericValue =
  | ExactValue<number>
  | ListValue<number>
  | ComparisonValue<number>
  | BoundedRange<number>
  | OpenRange<number>;

type IdValue = ExactValue<number>;

type ExactValue<T> = {
  kind: "ExactValue";
  value: T;
};

type ListValue<T> = {
  kind: "ListValue";
  values: T[];
};

type ComparisonOp = "eq" | "lt" | "lte" | "gt" | "gte";

type ComparisonValue<T> = {
  kind: "ComparisonValue";
  op: ComparisonOp;
  value: T;
};

type BoundedRange<T> = {
  kind: "BoundedRange";
  min: T;
  max: T;
  minInclusive: true;
  maxInclusive: true;
};

type OpenRange<T> = {
  kind: "OpenRange";
  min?: T;
  max?: T;
  minInclusive?: boolean;
  maxInclusive?: boolean;
};

type BooleanValue = {
  kind: "BooleanValue";
  value: boolean;
};

type IdentifierValue = {
  kind: "IdentifierValue";
  value: string;
};

type UserRef =
  | {
      kind: "UserName";
      name: string;
    }
  | {
      kind: "UserId";
      id: number;
      syntax: "bang" | "user_id";
    }
  | {
      kind: "CurrentUser";
      value: "me";
    };

type CollectionRef =
  | {
      kind: "CollectionId";
      id: number;
    }
  | {
      kind: "CollectionName";
      name: string;
    };

type TextPattern = {
  kind: "TextPattern";
  raw: string;
  normalized: string;
  quoted: boolean;
  wildcardMode: "none" | "prefix" | "suffix" | "contains" | "glob";
  caseSensitivity: "case-insensitive" | "case-sensitive" | "backend-default";
};

type SizeUnit = "B" | "KB" | "MB";

type SizeValue = {
  kind: "SizeValue";
  raw: string;
  bytes: number;
  unit: SizeUnit;
};

type SizeRangeValue = {
  kind: "SizeRangeValue";
  min: SizeValue;
  max: SizeValue;
  minInclusive: true;
  maxInclusive: true;
};

type RatioValue =
  | {
      kind: "RatioPair";
      width: number;
      height: number;
      decimal: number;
      rounding: 2;
    }
  | {
      kind: "RatioDecimal";
      decimal: number;
      rounding: 2;
    };

type DateEvaluationContext = {
  now: string;
  timezone: string;
  boundaryMode: "calendar" | "rolling";
};

type DateValue =
  | AbsoluteDateValue
  | NamedRelativeDateValue
  | RelativePeriodDateValue
  | AgoDateValue
  | YesterAgoDateValue;

type AbsoluteDateValue = {
  kind: "AbsoluteDate";
  date: string;
  originalFormat: "iso" | "named";
};

type NamedRelativeDateValue = {
  kind: "NamedRelativeDate";
  name:
    | "today"
    | "yesterday"
    | "yesterweek"
    | "yestermonth"
    | "yesteryear";
};

type RelativePeriodDateValue = {
  kind: "RelativePeriodDate";
  unit: "day" | "week" | "month" | "year" | "decade";
  amount: 1;
};

type AgoDateValue = {
  kind: "AgoDate";
  amount: number;
  unit: "days" | "weeks" | "months" | "years";
};

type YesterAgoDateValue = {
  kind: "YesterAgoDate";
  amount: number;
  unit: "weeks" | "months" | "years";
};

type DateRangeValue = {
  kind: "DateRangeValue";
  start?: DateValue;
  end?: DateValue;
  interpretation:
    | "absolute-range"
    | "relative-range"
    | "ago-range"
    | "yester-range"
    | "decade-compatibility-special-case";
};

type NullPolicy =
  | "match-null"
  | "exclude-null"
  | "nulls-first"
  | "nulls-last"
  | "backend-default";

type PermissionRequirement =
  | "logged-in-user"
  | "same-user-only"
  | "staff-only"
  | "public";

/* ============================================================================
 * Query Options
 * ========================================================================== */

type QueryOptions = {
  order?: OrderSpec;
  limit?: LimitSpec;
  randSeed?: RandSeedSpec;
  hotFrom?: HotFromSpec;
};

type ResolvedQueryOptions = {
  order: OrderSpec;
  limit?: LimitSpec;
  randSeed?: RandSeedSpec;
  hotFrom?: HotFromSpec;
};

type OrderKey =
  | "id"
  | "score"
  | "favcount"
  | "comment_count"
  | "comment"
  | "comment_bumped"
  | "mpixels"
  | "filesize"
  | "aspect_ratio"
  | "change"
  | "duration"
  | "random"
  | "hot"
  | "created_at"
  | "updated_at"
  | "note"
  | "tagcount"
  | "general_tags"
  | "artist_tags"
  | "contributor_tags"
  | "copyright_tags"
  | "character_tags"
  | "species_tags"
  | "invalid_tags"
  | "meta_tags"
  | "lore_tags"
  | "md5";

type OrderDirection = "asc" | "desc" | "none";

type OrderSpec = {
  kind: "OrderSpec";
  raw: string;
  rawAlias: string;
  canonicalKey: OrderKey;
  direction: OrderDirection;
  negated: boolean;
  reversible: boolean;
  requiresAuxiliaryData: boolean;
  nullPolicy?: NullPolicy;
  span: SourceSpan;
};

type LimitSpec = {
  kind: "LimitSpec";
  value: number;
  span: SourceSpan;
};

type RandSeedSpec = {
  kind: "RandSeedSpec";
  value: number;
  deterministicPagination: true;
  span: SourceSpan;
};

type HotFromSpec = {
  kind: "HotFromSpec";
  value: DateValue;
  span: SourceSpan;
};

/* ============================================================================
 * Compatibility State
 * ========================================================================== */

type CompatibilityState = {
  profile: "e621-search";

  termCount: {
    rawTerms: number;
    tags: number;
    metatags: number;
    expandedWildcardTerms: number;
    totalCountedTerms: number;
    maxAllowed: 40;
  };

  wildcards: {
    positiveWildcardCount: number;
    negatedWildcardCount: number;
    maxPositiveWildcards: 1;
    wildcardOrFlattening: boolean;
    tildeWildcardSuppressedExpansion: boolean;
  };

  groups: {
    maxAllowedDepth: 10;
    observedMaxDepth: number;
  };

  implicitDeletedFilter: {
    state: "enabled" | "suppressed";
    suppressedBy: SourceSpan[];
    injectedPredicateId?: string;
  };

  statusScopes: Record<
    ScopeId,
    {
      statusSlot?: StatusTerm;
      conflicts: StatusTerm[];
    }
  >;

  quotedMetatagValues: SourceSpan[];

  compatibilityAmbiguities: CompatibilityAmbiguity[];
};

type CompatibilityAmbiguity = {
  area: "order:id" | "date:decade" | "backend-text-search" | "other";
  message: string;
  span?: SourceSpan;
};

/* ============================================================================
 * Phase 3: Query Plan
 * ========================================================================== */

type QueryPlan = {
  target: BackendTarget;
  filter: Expr;
  injectedFilters: InjectedPredicate[];
  options: ResolvedQueryOptions;
  requiredIndexes: RequiredIndex[];
  dataDependencies: DataDependency[];
  diagnostics: Diagnostic[];
};

type BackendTarget = "json" | "sqlite" | "generic";

type InjectedPredicate =
  | {
      kind: "ImplicitDeletedFilter";
      id: string;
      predicate: StatusPredicatePlan;
      reason: "default-e621-search-behavior";
    }
  | {
      kind: "ResolvedAliasPredicate";
      id: string;
      predicate: Expr;
    }
  | {
      kind: "ResolvedImplicationPredicate";
      id: string;
      predicate: Expr;
    };

type StatusPredicatePlan = {
  field: "flags.deleted";
  op: "eq";
  value: false;
};

/* ============================================================================
 * Data Dependencies
 * ========================================================================== */

type DataDependency =
  | { kind: "AliasGraph" }
  | { kind: "ImplicationGraph" }
  | { kind: "TagPopularityIndex" }
  | { kind: "TagCategoryIndex" }
  | { kind: "UserIndex" }
  | { kind: "FavoritesIndex"; user?: UserRef }
  | { kind: "VotesIndex"; viewerRequired: boolean }
  | { kind: "ApprovalsIndex" }
  | { kind: "DeletionMetadata" }
  | { kind: "CommentsIndex" }
  | { kind: "NotesIndex" }
  | { kind: "PoolIndex" }
  | { kind: "SetIndex" }
  | { kind: "ReplacementIndex" }
  | { kind: "ArtistVerificationIndex" }
  | { kind: "HotScoreIndex" }
  | { kind: "PostCoreFields" };

type RequiredIndex =
  | { kind: "PostIdIndex" }
  | { kind: "TagIndex"; category?: TagCategory }
  | { kind: "TagClosureIndex" }
  | { kind: "TagPrefixIndex" }
  | { kind: "NumericFieldIndex"; field: NumericField | DimensionField | TagCountField }
  | { kind: "DateIndex"; field: "created_at" | "updated_at" }
  | { kind: "TextIndex"; field: TextSearchField }
  | { kind: "UserIndex" }
  | { kind: "CollectionIndex"; collection: "pool" | "set" }
  | { kind: "HashIndex"; algorithm: "md5" }
  | { kind: "OrderIndex"; key: OrderKey };

```


| AST component            | Required nodes / structs                                                                                                                                                                                                                                                                                      |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Root                     | `Query`, `QueryOptions`, `CompatibilityState`, `Diagnostic`, `DataDependency`                                                                                                                                                                                                                                 |
| Boolean expressions      | `All`, `Any`, `LooseOrBucket`, `Not`, `RootScope`, `GroupScope`                                                                                                                                                                                                                                               |
| Tag predicates           | `TagTerm`, `TagWildcard`, `ResolvedTagExpansion`                                                                                                                                                                                                                                                              |
| Typed metatag predicates | `StatusTerm`, `RatingTerm`, `FileTypeTerm`, `NumericFieldTerm`, `TagCountTerm`, `DimensionTerm`, `RatioTerm`, `FileSizeTerm`, `DateTerm`, `TextSearchTerm`, `UserTerm`, `ViewerStateTerm`, `RelationshipTerm`, `LockTerm`, `PresenceTerm`, `CollectionTerm`, `HashTerm`, `DurationTerm`, `AuxiliaryPredicate` |
| Value nodes              | `RangeValue`, `ListValue`, `ComparisonValue`, `BoundedRange`, `DateValue`, `DateRangeValue`, `SizeValue`, `RatioValue`, `BooleanValue`, `UserRef`, `TextPattern`, `IdentifierValue`                                                                                                                           |
| Query options            | `OrderSpec`, `LimitSpec`, `RandSeedSpec`, `HotFromSpec`                                                                                                                                                                                                                                                       |
| Compatibility metadata   | `StatusSlot`, `ImplicitDeletedFilter`, `TermCounter`, `WildcardCounter`, `NestingDepth`, `SourceSpan`, `RawToken` / `CSTNode`                                                                                                                                                                                 |






## 1) Fix invalid cache issues: 
Right now there are cases where posts can be downloaded but not have any image associated with the downloaded post. 

We should fix image downloading to default to available file when requested --size is not available. Sometimes when we're downloading files, there is no sample actually available. Original should always be the fallback to ensure there is always an image when a post is downloaded.

* In cases where the requested size is not available, we should automatically download the original file and update the manifest. So for example, if the `sample` size is not available for an image, we download the `original` instead, and save that image under the `original` and `sample` directories, then complete the entries for both `original` and `sample` in the manifest. 

* Add a --repair to `fetch` command that goes and fetches any missing images in the cache according to ^. Useful for healing if the images are deleted anyways, and useful for buttoning up a few edgecases around the next change too.



## 2) Update fetching flow.
Right now I believe we do something like:

1. fetch a page
2. Check our out-dir for those entries
2. download images for that page
3. fetch next page

This has a few problems:
* If our out-dir becomes excessively large (say 50k+ images), checking our cache becomes expensive.
* There is no progress reported, and it's not clear how many items are left.
* Resumability is tough to orchestrate per query.

I'd like to change fetching semantics a little bit so that we do something like:

1. Fetch all pages, store any posts per page in `posts/` directory (overwriting existing files if they exist)
2. Create a json file for that search in `queries/[md5 hash of query].json`
3. Filter out any
3. Begin downloading all post IDs



we now have a new file in the outdir named `queries`:

```
images/
json/
queries/
manifest.json
```



In `queries`, we store all of the post ids associated with 





3. Create a "queue" command that works like fetch


3. Add `search` command. This command should work exactly like "fetch" does syntax wise, except it searches among the downloaded files. It should support all of e621's natural language queries, such as:
```
Cheatsheet

This page lists every metatag available for tagging and searching, as well as additional information about syntax. For basic information about tags, what they are, and how to use them, see the tag help page.
These tags and syntaxes are used to search for posts.
 
↑ Basics

cat dog
Search for posts that are tagged with both cat and dog. Tags are separated by spaces. You can only have up to 40 tags1 in a single search.

red_panda african_wild_dog
Words within each tag are separated by underscores.

~cat ~dog
Search for posts that are tagged either cat or dog (or both). May not work well when combined with other syntaxes2.

-chicken
Search for posts that don't have the chicken tag.

fox -chicken
Search for posts that are tagged with fox and are not tagged with chicken.

african_*
Search for posts with any tag that starts with african_, such as african_wild_dog or african_golden_cat. May not work well when combined with other syntaxes, and may give incomplete results2. Do not rely on wildcards being perfect.
Limit one wildcard per search.

-african_*
Search for posts with no tags that start with african_, such as african_wild_dog or african_golden_cat.
There are no limits for how many negated wildcards can be used in a search.

( ~cat ~tiger ~leopard ) ( ~dog ~wolf )
Search for posts that are tagged with one (or more) of cat, tiger or leopard, and one (or more) of dog or wolf.

    ⓘ Important

    The opening parenthesis must be followed by a space, and the closing parenthesis must follow a space; this requirement prevents them from being mistaken for a tag that includes a parenthesis (like many character tags do).

The Power of Implications

Many tags are implied by other tags, and in cases like this example, searching for the implied tag might be better than trying to list everything that tag implies. For example, cat is aliased to domestic cat, and domestic cat, tiger and leopard are all members of Felidae, the cat family, and thus all imply felid, while dog (aliased to domestic dog) & wolf both imply Canid (the dog family). When trying to find posts with any kind of feline and any kind of canine, try searching for felid canid instead. You can find a tag's aliases & implications listed at the bottom of the tag's wiki page, you can search for aliases & implications directly, and you can use our Tag MetaSearch to search for tags, aliases, & implications at the same time.
Implication Chains

Advanced Group Syntax

    The aforementioned - & ~ prefixes can be used on groups themselves. For example, ~( felid -leopard ) ~( leopard tiger ) will find posts with felids, but won't allow any posts with a leopard unless there's also a tiger.
    Groups can also be nested within one another. For example, ( ~( felid -leopard ) ~( leopard tiger ) ) dog will return posts with either non-leopard felids or with both a leopard & a tiger, and then keep only posts that also have a dog.
        You can only nest groups 10 levels deep.

    Most searches automatically remove deleted posts from results; see here for more info.

[[e621:cheatsheet#Basics]]
Metatags

In addition to using tags, you can also search based on the post's metadata using the appropriate metatag3. The remainder of this guide is focused on the available metatags you can use.4

    ⚠ Warning

    Do not assume using these on the blacklist will work; many of these are not available on the blacklist, & some that are behave differently than when used while searching. Please see here for the list of blacklist metatags & their differences.

↑ Sorting

By default, posts are ordered from highest ID to lowest ID (newest posts first). This is the same as order:id.
Adding the "limit:" metatag will increase or decrease the number of posts per page, otherwise following the normal rules for your current sorting method.

    votedup:me order:random limit:1 will return a single post that you have upvoted, with no pagination.
    order:id_asc limit:1 will display the single oldest post, with the next pages containing the second oldest, the third oldest, etc.
    limit:10 will display the 10 newest posts, with the next pages containing posts 11 through 20, 21 through 30, etc.

Search 	What it does 	Search 	What it does
order:id 	Oldest to newest 	order:id_desc 	Newest to oldest
order:score 	Highest score first 	order:score_asc 	Lowest score first
order:favcount 	Most favorites first 	order:favcount_asc 	Least favorites first
order:comment_count 	Most comments first 	order:comment_count_asc 	Least comments first
order:comment_bumped 	Posts with the newest bumped comments 	order:comment_bumped_asc 	Posts that have not had bumped comments for the longest time
order:mpixels 	Largest resolution first 	order:mpixels_asc 	Smallest resolution first
order:filesize 	Largest file size first 	order:filesize_asc 	Smallest file size first
order:landscape 	Wide and short to tall and thin 	order:portrait 	Tall and thin to wide and short
order:change 	Sorts by last update sequence, highest to lowest 	order:change_asc 	Sorts by last update sequence, lowest to highest
order:duration 	Video duration longest to shortest 	order:duration_asc 	Video duration shortest to longest
order:random 	Orders posts randomly * 	order:hot 	The order used by the 'Hot' page **
order:comment 	Posts with the newest comments 	order:comment_asc 	Posts that have not been commented on for the longest time
order:created 	Newest post date first 	order:created_asc 	Oldest post date first
order:updated 	Most recently updated post first 	order:updated_asc 	Least recently updated post first
order:note 	Most recent note first 	order:note_asc 	No note/oldest note first
order:tagcount 	Most tags first 	order:tagcount_asc 	Least tags first
order:general_tags 	Most general tags first 	order:general_tags_asc 	Least general tags first
order:artist_tags 	Most artist tags first 	order:artist_tags_asc 	Least artist tags first
order:contributor_tags 	Most contributor tags first 	order:contributor_tags_asc 	Least contributor tags first
order:copyright_tags 	Most copyright tags first 	order:copyright_tags_asc 	Least copyright tags first
order:character_tags 	Most character tags first 	order:character_tags_asc 	Least character tags first
order:species_tags 	Most species tags first 	order:species_tags_asc 	Least species tags first
order:invalid_tags 	Most invalid tags first 	order:invalid_tags_asc 	Least invalid tags first
order:meta_tags 	Most meta tags first 	order:meta_tags_asc 	Least meta tags first
order:lore_tags 	Most lore tags first 	order:lore_tags_asc 	Least lore tags first
order:md5 	Highest MD5 checksum to lowest 	order:md5_asc 	Lowest MD5 checksum to highest

    * If you need deterministic results, you can use randseed:123 instead; passing the same seed will return the same set of random posts. This supports pagination, and the following pages will contain no duplicates. Seeds must be numbers.
    ** You can use the hot_from metatag with a date (see below for accepted date formats) to change the start of the 2 day window order:hot sorts posts over.

Almost all orders can be reversed by adding a - before the metatag. For example, -order:score is equivalent to order:score_asc, and -order:score_asc is equivalent to order:score. If the order category isn't capable of being reversed, it will be equivalent to the non-negated version.
Full List (including shorthand variants)

Many of these have alternative forms that are removed from the autocomplete to minimize bloat. Here are all valid order metatags, with each item in a cell being equivalent to every other item in that cell.
Category 	Main Ordering 	Reversed Ordering
Creation Date 	order:created_at, order:created_at_desc, -order:created_at_asc, order:created, order:created_desc, -order:created_asc 	-order:created_at, -order:created_at_desc, order:created_at_asc, -order:created, -order:created_desc, order:created_asc
Update Date 	order:updated_at, order:updated_at_desc, -order:updated_at_asc, order:updated, order:updated_desc, -order:updated_asc 	-order:updated_at, -order:updated_at_desc, order:updated_at_asc, -order:updated, -order:updated_desc, order:updated_asc
Comment 	order:comm, order:comm_desc, -order:comm_asc, order:comment, order:comment_desc, -order:comment_asc 	-order:comm, -order:comm_desc, order:comm_asc, -order:comment, -order:comment_desc, order:comment_asc
Comment Bumped 	order:comm_bumped, order:comm_bumped_desc, -order:comm_bumped_asc, order:comment_bumped, order:comment_bumped_desc, -order:comment_bumped_asc 	-order:comm_bumped, -order:comm_bumped_desc, order:comm_bumped_asc, -order:comment_bumped, -order:comment_bumped_desc, order:comment_bumped_asc
Comment Count 	order:comm_count, order:comm_count_desc, -order:comm_count_asc, order:comment_count, order:comment_count_desc, -order:comment_count_asc 	-order:comm_count, -order:comm_count_desc, order:comm_count_asc, -order:comment_count, -order:comment_count_desc, order:comment_count_asc
Filesize 	order:size, order:size_desc, -order:size_asc, order:filesize, order:filesize_desc, -order:filesize_asc 	-order:size, -order:size_desc, order:size_asc, -order:filesize, -order:filesize_desc, order:filesize_asc
Aspect Ratio 	order:ratio, order:ratio_desc, -order:ratio_asc, order:aspect_ratio, order:aspect_ratio_desc, -order:aspect_ratio_asc, -order:portrait, order:landscape 	-order:ratio, -order:ratio_desc, order:ratio_asc, -order:aspect_ratio, -order:aspect_ratio_desc, order:aspect_ratio_asc, order:portrait, -order:landscape
Resolution 	order:mpixels, order:mpixels_desc, -order:mpixels_asc 	-order:mpixels, -order:mpixels_desc, order:mpixels_asc
General Tags 	order:general_tags, order:general_tags_desc, -order:general_tags_asc, order:gentags, order:gentags_desc, -order:gentags_asc 	-order:general_tags, -order:general_tags_desc, order:general_tags_asc, -order:gentags, -order:gentags_desc, order:gentags_asc
Artist Tags 	order:artist_tags, order:artist_tags_desc, -order:artist_tags_asc, order:arttags, order:arttags_desc, -order:arttags_asc 	-order:artist_tags, -order:artist_tags_desc, order:artist_tags_asc, -order:arttags, -order:arttags_desc, order:arttags_asc
Contributor Tags 	order:contributor_tags, order:contributor_tags_desc, -order:contributor_tags_asc, order:conttags, order:conttags_desc, -order:conttags_asc 	-order:contributor_tags, -order:contributor_tags_desc, order:contributor_tags_asc, -order:conttags, -order:conttags_desc, order:conttags_asc
Copyright Tags 	order:copyright_tags, order:copyright_tags_desc, -order:copyright_tags_asc, order:copytags, order:copytags_desc, -order:copytags_asc 	-order:copyright_tags, -order:copyright_tags_desc, order:copyright_tags_asc, -order:copytags, -order:copytags_desc, order:copytags_asc
Character Tags 	order:character_tags, order:character_tags_desc, -order:character_tags_asc, order:chartags, order:chartags_desc, -order:chartags_asc 	-order:character_tags, -order:character_tags_desc, order:character_tags_asc, -order:chartags, -order:chartags_desc, order:chartags_asc
Species Tags 	order:species_tags, order:species_tags_desc, -order:species_tags_asc, order:spectags, order:spectags_desc, -order:spectags_asc 	-order:species_tags, -order:species_tags_desc, order:species_tags_asc, -order:spectags, -order:spectags_desc, order:spectags_asc
Invalid Tags 	order:invalid_tags, order:invalid_tags_desc, -order:invalid_tags_asc, order:invtags, order:invtags_desc, -order:invtags_asc 	-order:invalid_tags, -order:invalid_tags_desc, order:invalid_tags_asc, -order:invtags, -order:invtags_desc, order:invtags_asc
Meta Tags 	order:meta_tags, order:meta_tags_desc, -order:meta_tags_asc, order:metatags, order:metatags_desc, -order:metatags_asc 	-order:meta_tags, -order:meta_tags_desc, order:meta_tags_asc, -order:metatags, -order:metatags_desc, order:metatags_asc
Lore Tags 	order:lore_tags, order:lore_tags_desc, -order:lore_tags_asc, order:lortags, order:lortags_desc, -order:lortags_asc 	-order:lore_tags, -order:lore_tags_desc, order:lore_tags_asc, -order:lortags, -order:lortags_desc, order:lortags_asc
Id 	order:id, -order:id_desc, order:id_asc 	-order:id, order:id_desc, -order:id_asc
Score 	order:score, order:score_desc, -order:score_asc 	-order:score, -order:score_desc, order:score_asc
MD5 	order:md5, order:md5_desc, -order:md5_asc 	-order:md5, -order:md5_desc, order:md5_asc
Favorite Count 	order:favcount, order:favcount_desc, -order:favcount_asc 	-order:favcount, -order:favcount_desc, order:favcount_asc
Note 	order:note, order:note_desc, -order:note_asc 	-order:note, -order:note_desc, order:note_asc
Tag Count 	order:tagcount, order:tagcount_desc, -order:tagcount_asc 	-order:tagcount, -order:tagcount_desc, order:tagcount_asc
Change Seq 	order:change, order:change_desc, -order:change_asc 	-order:change, -order:change_desc, order:change_asc
Duration 	order:duration, order:duration_desc, -order:duration_asc 	-order:duration, -order:duration_desc, order:duration_asc
Random 	order:random, -order:random 	No Reversed Ordering Supported
Hot 	order:hot, -order:hot 	No Reversed Ordering Supported

[[e621:cheatsheet#Sorting]]
↑ User-Based Metatags

These metatags are used to search for posts based on user-related information.

The basic form is metatag:username (e.g., favoritedby:Bob). You can also search for these by the user's id by adding an exclamation point before the id. For example, user:!17633 searches for posts uploaded by a user with the ID # 17633, rather than the name "17633".
Search 	What it does
user_id:17633* 	Posts uploaded by a user with the ID # 17633, rather than the name "17633"
user:Bob 	Posts uploaded by Bob
fav:Bob/favoritedby:Bob 	Posts favorited by Bob; will not work for anyone but Bob if they have their favorites hidden
voted:anything** 	Posts you voted on. Only works while logged in.
votedup:anything/upvote:anything** 	Posts you upvoted. Only works while logged in.
voteddown:anything/downvote:anything** 	Posts you downvoted. Only works while logged in.
approver:Bob 	Posts approved by Bob
deletedby:Bob 	Posts deleted by Bob
commenter:Bob/comm:Bob 	Posts commented on by Bob
noter:Bob 	Posts with notes written by Bob
noteupdater:Bob 	Posts with notes updated by Bob

    * This metatag cannot use the metatag:!user_id syntax.
    ** anything can be replaced with any text; it will only return results for the logged-in user. Only staff members can search another user's votes.

[[e621:cheatsheet#UserMetatags]]
↑ Post-Based Metatags

These metatags are used to search for posts based on post-related information.
Counts

Tag counts 	What it does 	Supported syntax
id:100 	Post with an ID of 100 	range
score:100 	Posts with a score of 100 	range
favcount:100 	Posts with exactly 100 favorites 	range
comment_count:100 	Posts with exactly 100 comments 	range
tagcount:2 	Posts with exactly 2 tags 	range
gentags:2 	Posts with exactly 2 general tags 	range
arttags:2 	Posts with exactly 2 artist tags 	range
conttags:2 	Posts with exactly 2 contributor tags 	range
copytags:2 	Posts with exactly 2 copyright tags 	range
chartags:2 	Posts with exactly 2 character tags 	range
spectags:2 	Posts with exactly 2 species tags 	range
invtags:2 	Posts with exactly 2 invalid tags 	range
metatags:2 	Posts with exactly 2 meta tags 	range
lortags:2 	Posts with exactly 2 lore tags 	range

[[e621:cheatsheet#Counts]]
Rating

Rating 	Shorthand 	What it does
rating:safe 	rating:s 	Posts rated safe
rating:questionable 	rating:q 	Posts rated questionable
rating:explicit 	rating:e 	Posts rated explicit

[[e621:cheatsheet#Rating]]
File Types

File type 	What it does
type:jpg 	Posts that are JPG, a type of image
type:png 	Posts that are PNG, a type of image (may be animated; see animated_png)
type:gif 	Posts that are GIF, a type of image (frequently animated)
type:webp 	Posts that are WebP, a type of image (may be animated)
type:mp4 	Posts that are MP4, a type of video
type:swf 	Posts that are Flash, a legacy format used for animation & interactivity
type:webm 	Posts that are WebM, a type of video

[[e621:cheatsheet#FileTypes]]
Image Size

Image Size 	What it does 	Supported syntax
width:100 	Posts with a width of 100 pixels 	range
height:100 	Posts with a length of 100 pixels 	range
mpixels:1 	Posts that are 1 megapixel (a 1000x1000 image equals 1 megapixel) 	range
ratio:4:3 	Search for posts with a ratio of 4:3. This also supports decimal values (e.g. ratio:1.33). All ratios are rounded to two digits, therefore 1.33 will return posts with a ratio of 4:3. 	range
filesize:200KB 	Posts with a file size of 200 kilobytes. File sizes within ±5% of the value are included. 	range
filesize:2MB 	Posts with a file size of 2 megabytes. File sizes within ±5% of the value are included. 	range

[[e621:cheatsheet#ImageSize]]
Post Status

Post status 	What it does
status:pending 	Posts that are waiting to be approved or deleted
status:active* 	Posts that have been approved
status:deleted* 	Posts that have been deleted
status:flagged 	Posts that are flagged for deletion
status:modqueue 	Posts that are pending or flagged
status:any*/status:all* 	All active or deleted posts

    * Disables implicit filtering of deleted posts from results. Note that adding -status:deleted to a search outside of a group will disable implicit filtering, but still explicitly remove all deleted posts from the results. Adding a deletedby metatag or delreason metatag with any value will also disable this.

Advanced Details

[[e621:cheatsheet#Status]]
Dates

Single day 		What it does 	Supported syntax
date:2012-04-27 	date:april/27/2012 	Search for posts uploaded on a specific date; assumes year-month-day format by default. 	range
date:today 	date:yesterday 	Same as above, but specific to today and yesterday 	range

Simple time period 	What it does 	Supported syntax
date:day 	Posts from the last day 	range, ago, yester
date:week 	Posts from the last 7 days 	range, ago, yester
date:month 	Posts from the last 30 days 	range, ago, yester
date:year 	Posts from the last 365 days 	range, ago, yester
date:decade 	Posts from the last decade 	range*

    * Only x..decade is supported (e.g., date:decade..year)

Ago syntax 	What it does 	Supported syntax
date:5_days_ago 	Posts from within the last 5 days 	range**
date:5_weeks_ago 	Posts from within the last 5 weeks 	range**, yester
date:5_months_ago 	Posts from within the last 5 months 	range**, yester
date:5_years_ago 	Posts from within the last 5 years 	range**, yester

    ** If used as the first of a range, will start at the first day of that range. For example, date:year..month will range from 30 days ago to 1 year ago.

Yester syntax 	What it does 	Supported syntax
date:yesterweek 	Posts from last week 	ago
date:yestermonth 	Posts from last month 	ago
date:yesteryear 	Posts from last year 	ago
date:5_yesteryears_ago 	Posts from 5 years ago 	

[[e621:cheatsheet#Dates]]
Text Searching

Text searching 	What it does
source:*example.com 	Posts with a source that contains example.com, prefix matched, use wildcards as needed
source:none 	Posts without a source
description:whatever * 	Posts with a description that contains the text whatever.
note:whatever * 	Posts with a note that contains the text whatever
delreason:*whatever * 	Deleted posts that contain a reason with the text whatever, prefix matched, use wildcards as needed

    * These can search for text with spaces in it by wrapping the text in " (e.g. description:"hello there" will search for posts with a description that contains the text hello there).4

[[e621:cheatsheet#TextSearching]]
```