# six2one

<p align="center">
  <img src="https://github.com/nollafox/six2one/raw/main/docs/banner.png" alt="six2one banner" style="border-radius: 16px; box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12); max-width: 100%; height: auto;">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-1E90FF" alt="Python 3.10+">
  <a href="https://github.com/nollafox/six2one/actions/workflows/test.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/nollafox/six2one/test.yml?branch=main&label=tests&color=2E8B57" alt="Test status">
  </a>
  <img src="https://img.shields.io/badge/CLI-621-4169E1" alt="621 CLI">
  <img src="https://img.shields.io/badge/site-e621-8A2BE2" alt="e621">
  <img src="https://img.shields.io/badge/author-Nolla%20Fox-2E8B57" alt="Author: Nolla Fox">
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#local-first-search">Local-first Search</a> •
  <a href="#how-queries-work">Queries</a> •
  <a href="#local-storage">Storage</a> •
  <a href="#commands">Commands</a>
</p>

You've never met an e621 downloader quite like this one. **six2one** turns ordinary e621 searches into a local, reusable cache: post JSON in SQLite, image variants on disk, and just-in-time sidecar data for richer queries.

The cache itself is what separates `six2one` from other download scripts and packages. Each query leaves its results behind — post JSON, sidecar data, images — and over time the store grows into a searchable local archive of the parts of e621 you actually use. Later queries reuse whatever earlier ones already pulled, so nothing is fetched or downloaded twice.

The result is a tool that stays light for a quick search and dependable for collections you return to over weeks: an artist reference folder, an archive of a saved query, or a training set carved from a larger cache. Eventually, **your searches grow into a local, curated mirror of the e621 tags, posts, and metadata you actually use.**

## Quick Start

Install six2one from PyPI:

```bash
$ python -m pip install six2one
```

This puts the `621` command on your `PATH`. Then you can set up the local workspace alongside your credentials:

```bash
$ 621 bootstrap          # initialize the local workspace
$ 621 auth               # store your e621 API credentials
```

six2one allows you to **fetch broadly, and export narrowly.** Fetch one wide query, then slice it as many ways as you need — by score, by enrichment, by pool — without touching the network again:

```bash
$ 621 fetch "dragon rating:s" --limit 1000
$ 621 export "dragon rating:s score:>100" -o ./best-dragons
$ 621 export "dragon rating:s notes:any" -o ./noted-dragons
$ 621 export "dragon rating:s pool:*" -o ./pool-dragons
```

If you simply want to browse the entire collection, images are stored in `~/.six2one` before exporting.

## How Queries Work

six2one speaks e621's own post search syntax, so there is no new query language to learn. It supports every construct on e621's [post search syntax cheatsheet](https://e621.net/help/cheatsheet): negated tags, loose-OR terms, nested groups, metatags, ranges, sorting, ratings, status filters, wildcards, aliases, and implications. Before anything runs, six2one can explain the query in plain language: what must match, what is excluded, how aliases and implications expand, and what extra data may need to be cached.

```bash
$ 621 query explain "fox ( ~dog ~cat )"
```

```text
Query
  canine ( ~dog ~cat )

Meaning
  1. The post must match canine. More-specific tags that imply canine can also match.
  2. Inside the group, the post only needs to match one option: domestic_dog or domestic_cat.
  3. Results are ordered by post id, descending.

Tag matching
  canine
    Matches posts tagged canine.
    Also matches posts with more-specific tags that imply canine, 
    such as canis, fox, mythological_canine, and 434 other tags.

  dog → domestic_dog
    Matches posts tagged domestic_dog, because dog is an alias.
    Also matches posts with more-specific tags that imply domestic_dog, 
    such as spitz, pastoral_dog, hunting_dog, and 286 other tags.

  cat → domestic_cat
    Matches posts tagged domestic_cat, because cat is an alias.
    Also matches posts with more-specific tags that imply domestic_cat, 
    such as calico_cat, tabby_cat, hairless_cat, and 65 other tags.
```

This feature is what keeps fetches efficient, and ensures offline search works the same way it does on e621. If a query can be answered from cached post fields, six2one uses them directly. If it needs richer data, such as comments, notes, pools, sets, or favorites, six2one fetches that data once and stores it with the matching posts.

In other words, simple queries only cache posts, complex queries automatically cache additional data as you go:
```bash
 # Only caches posts and downloads images
$ 621 fetch "dragon rating:s" --limit 100

# This query needs comment data, so six2one fetches and stores it
$ 621 fetch "dragon rating:s comments:any" --limit 100 

# This query reuses any cached posts, images, and comments
$ 621 fetch "dragon rating:s comments:any order:score" --limit 100 
```

Enrichment is cached by post, not by query. Once six2one has comments for post `6407238`, any future query that needs that post’s comments can use the cached copy. Thus, overlapping searches become cheaper over time:

```bash
# Reuses any cached posts, images, and comments from the prior 3 commands
621 fetch "scales comments:any order:score" --limit 100 
```

## Local-first Search

The first time a query needs a piece of data, six2one fetches it from e621. After that the data is local, and the next query that needs it reads from the cache instead of the network. Broad fetches fill the archive, narrower queries carve it, and exports turn matching downloaded posts into portable folders — none of it re-fetching what is already on disk.

<p align="center">
  <img src="https://github.com/nollafox/six2one/raw/main/docs/flowchart.png" alt="six2one banner" style="border-radius: 16px; box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12); max-width: 100%; height: auto;">
</p>

A single run moves through the same stages, from a raw query to files on disk:

```text
query
  → compile the query
  → discover matching posts
  → cache post JSON
  → fetch missing enrichment
  → evaluate the query locally
  → queue and download images
  → export semantic subsets
```

Everything six2one caches lives under `~/.six2one`. The global cache is deliberate — it is what lets unrelated queries share data. Downloaded files are never scattered through your working directories; they stay in the workspace until you `export` them somewhere explicit.

## Local Storage

`bootstrap` creates the six2one workspace under `~/.six2one`:

```text
~/.six2one/
  config.toml
  bootstrap.json
  cache/
    six2one.sqlite
  images/
```

The SQLite database holds the cache:

| Data | Purpose |
|---|---|
| Cached posts | Raw post JSON and searchable post fields. |
| Source runs | Query runs and discovery metadata. |
| Queue jobs | Pending, running, failed, and completed work. |
| Enrichment coverage | Which sidecar data has already been fetched. |
| Tags | Tag metadata, aliases, implications, and categories. |
| Images | Where downloaded image variants live on disk. |

Images are stored on disk by post ID and variant, so if two queries match the same post and ask for the same variant, the image is downloaded only once:

```text
~/.six2one/images/
  000006407238/
    preview.jpg
    sample.jpg
    original.png
```

| Variant | e621 post field |
|---|---|
| `preview` | `post["preview"]` |
| `sample` | `post["sample"]` |
| `original` | `post["file"]` |

For each file, the database records which variant it is, where it was written, and the source URL it came from.

## Commands

```text
usage: 621 COMMAND [options]

Queue, enrich, and fetch e621 posts into the local six2one store.

commands:
  bootstrap   initialize the local six2one workspace
  auth        configure e621 API credentials
  query       inspect e621-style query syntax
  queue       discover and enqueue query work
  fetch       discover, enqueue, and download posts
  export      export downloaded images and cached post JSON
```

### Bootstrap

```bash
$ 621 bootstrap
```

`bootstrap` prepares the workspace: it writes the config file, initializes the SQLite store, runs migrations, and imports the e621 tag data that query binding needs for tag lookup, aliases, implications, and categories. Most other commands expect it to have run first.

### Auth

```bash
$ 621 auth
$ 621 auth --test
$ 621 auth --remove
```

`auth` stores the e621 API credentials used by network commands. `--test` verifies them; `--remove` deletes them.

### Query Explain

```bash
$ 621 query explain "fox ( ~dog ~cat )"
$ 621 query explain "score:>100 order:score rating:s" --compact
$ 621 query explain "dragon rating:e" --json
```

`query explain` parses, binds, and explains a query without touching the network. It reports the required and excluded tags, loose-OR groups, metatags, sorting, the data the query depends on, and any compatibility notes or diagnostics — the safest way to see what a long fetch will do before you start it.

### Queue

Use `queue` when you want to inspect, modify, or stage work before downloading. It runs discovery only: it finds matching posts, caches their post JSON, enqueues any enrichment the query needs, evaluates the query locally, and queues image downloads — without downloading them.

```bash
$ 621 queue "dragon rating:s" --limit 10
```

```text
six2one queue

Query
  dragon rating:s

Phase 1/1: Discovering posts
  pages                    48 / 48
  cached post JSON         3,812
  new image jobs           3,812
  already queued           0
  already downloaded       0
  skipped                  0

Queued.

Next
  Download queued images:
    621 fetch --queue
```

Inspect and manage queued work:

```bash
$ 621 queue list
$ 621 queue list --failed
$ 621 queue clear --failed --yes
$ 621 queue clear q_01HXW6T2KZ9A
```

`queue clear` also accepts a query. It is not text-matched against the original run — it is evaluated against the cached post data, and removes only the queued jobs whose posts match. `queue clear "canine -paws"`, for instance, drops the queued images for posts that are tagged `canine` but not also tagged `paws`:

```bash
$ 621 queue clear "young"
$ 621 queue clear "canine -paws"
$ 621 queue clear "rating:e dragon -animated"
```

`queue amend` does the inverse — instead of clearing from a run, it folds a new exclusion into the source run itself and updates the jobs that remain:

```bash
$ 621 queue amend q_01HXW6T2KZ9A --exclude "young"
$ 621 queue amend q_01HXW6T2KZ9A --exclude "canine -paws"
```

Either way, cached post JSON, downloaded images, and source-run metadata are left untouched; the run stays inspectable. A staged workflow looks like:

```bash
$ 621 queue "dragon rating:s" --limit 1000
$ 621 queue clear "young"
$ 621 queue amend q_01HXW6T2KZ9A --exclude "canine -paws"
$ 621 fetch --queue
```

### Fetch

```bash
$ 621 fetch "dragon rating:s" --limit 10
```

`fetch` runs both phases: everything `queue` does, then the download. It discovers posts, caches post JSON, fetches missing enrichment, evaluates the query, and writes the matching images to disk.

```text
six2one fetch

Query
  dragon rating:s

Phase 1/2: Discovering posts
  Fetching result pages       48 / 48
  Cached post JSON            3,812 posts
  New image jobs              3,812
  Already queued              0
  Already downloaded          0
  Skipped                     0

Phase 2/2: Downloading images
  Downloaded                  3,812 / 3,812
  Failed                      0
  Skipped existing files      0
  Written                     7.42 GB

Done.
```

`fetch` and `queue` both take `--limit N`, which caps a run at N posts; without it, six2one processes every page the query returns. Pick the image variant with `--file-type`:

```bash
$ 621 fetch "dragon rating:s" --file-type original
$ 621 fetch "dragon rating:s" --file-type sample
$ 621 fetch "dragon rating:s" --file-type preview
```

Run already-queued work with `--queue`, and retry failed jobs with `--retry-failed`. Failed jobs are kept for inspection until you retry or clear them.

```bash
$ 621 fetch --queue
$ 621 fetch --queue --retry-failed
```

### Export

```bash
$ 621 export "dragon rating:s" -o ./dragon-export
$ 621 export -o ./all-downloaded
```

`export` builds a clean folder of symlinks and cached post JSON. Given a query, it exports the downloaded images whose cached posts match; given none, it exports everything downloaded.

The query is the same e621 language `fetch` and `queue` use, not a filename filter, so export can carve precise subsets out of the store:

```bash
$ 621 export "dragon rating:s score:>100" -o ./high-score-dragons
$ 621 export "fox ( ~dog ~cat ) -comic" -o ./fox-animal-overlap
$ 621 export "notes:any rating:s" -o ./posts-with-notes
$ 621 export "pool:* order:score" -o ./pool-posts
```

If a subset query needs cached data that is not present yet — notes, comments, pool membership — export fetches that enrichment before filtering. It never downloads images, though: it links what is already on disk and reports what is missing.

```text
dragon-export/
  images/
    000006407238/
      original.png
      sample.jpg
      preview.jpg
  posts/
    000006407238.json
```

**Fetch broadly, export narrowly.** This is the workflow export is built for. Fetch one wide query, then slice it as many ways as you need — by score, by enrichment, by pool — without touching the network again:

```bash
$ 621 fetch "dragon rating:s" --limit 1000
$ 621 export "dragon rating:s score:>100" -o ./best-dragons
$ 621 export "dragon rating:s notes:any" -o ./noted-dragons
$ 621 export "dragon rating:s pool:*" -o ./pool-dragons
```

## Development

Install with Poetry, then run the CLI and tests from inside the environment:

```bash
$ poetry install
$ poetry run 621 --help
$ poetry run 621 query explain "fox ( ~dog ~cat )"
$ poetry run 621 fetch --help
$ poetry run pytest
$ poetry run python -m compileall -q src
```

<br>

***

<p align="center">
  <strong>six2one</strong> — local e621 fetching, enrichment, and export.
</p>

<p align="center">
  Crafted with ❤️ by <strong>Nolla Fox</strong>
</p>