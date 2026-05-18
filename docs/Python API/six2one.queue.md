# six2one.queue

The durable **job queue and runner** for six2one.

This package coordinates long-running six2one work: fetching post pages, enriching
sidecar data, evaluating queries, and downloading images. It is intentionally
small, explicit, and restart-safe. Queue jobs are durable recipe cards stored in
SQLite; job classes know how to execute those recipe cards; the runner wires them
to runtime services.

It is the conductor, not the orchestra.

---

## Contents

- [six2one.queue](#six2onequeue)
  - [Contents](#contents)
  - [Design](#design)
  - [Quick start](#quick-start)
  - [The queue model](#the-queue-model)
  - [Job payloads must be serializable](#job-payloads-must-be-serializable)
  - [Runtime context](#runtime-context)
  - [Queue](#queue)
    - [Enqueue](#enqueue)
    - [List](#list)
    - [Cancel](#cancel)
  - [Job](#job)
    - [JobResult](#jobresult)
  - [Registry](#registry)
  - [Runner](#runner)
  - [Built-in jobs](#built-in-jobs)
    - [Core pipeline](#core-pipeline)
    - [Tag/database lifecycle](#tagdatabase-lifecycle)
    - [Enrichment](#enrichment)
  - [Job state lifecycle](#job-state-lifecycle)
  - [Storage boundary](#storage-boundary)
  - [Writing a custom job](#writing-a-custom-job)
  - [Displaying jobs in the CLI](#displaying-jobs-in-the-cli)
  - [Retries and idempotency](#retries-and-idempotency)
  - [Package layout](#package-layout)
    - [File responsibilities](#file-responsibilities)
  - [What this package does not do](#what-this-package-does-not-do)

---

## Design

`six2one.queue` has four layers.

| Layer | Owns |
|---|---|
| `Queue` | ergonomic enqueue/list/cancel/run API |
| `Job` classes | executable units of work |
| `JobRegistry` | mapping job kind strings to job classes |
| `QueueRunner` | claiming durable jobs and running them with a runtime context |

The queue package does **not** talk to SQLite directly. Persistence goes through
the storage layer:

```text
six2one.queue
  calls
six2one.storage.stores.queue.QueueStore
  which owns SQL
```

This keeps the job system reusable without leaking SQL into job code.

---

## Quick start

```python
from six2one.e621 import E621Client
from six2one.queue import Queue, QueueRunner, JobContext, default_registry
from six2one.storage import Storage

with Storage.open("~/.six2one/cache/six2one.sqlite") as storage:
    e621 = E621Client(
        auth=("username", "api_key"),
        user_agent="six2one/0.1 by username",
    )

    queue = Queue(
        storage=storage,
        registry=default_registry(),
    )

    queue.enqueue(
        "enrich_comments",
        {
            "post_ids": [6407238],
            "source_run_id": "q_01HXW6T2KZ9A",
        },
    )

    runner = QueueRunner(
        queue=queue,
        context=JobContext(
            storage=storage,
            e621=e621,
            settings=None,
            logger=None,
        ),
        worker_id="worker-1",
    )

    runner.run_until_empty()
```

The durable queue row contains only:

```json
{
  "kind": "enrich_comments",
  "payload_json": {
    "post_ids": [6407238],
    "source_run_id": "q_01HXW6T2KZ9A"
  }
}
```

The `E621Client`, `Storage`, logger, settings, and other services are supplied at
runtime through `JobContext`.

---

## The queue model

A queue job is a durable row stored in the main six2one SQLite database.

Conceptually:

```text
QueueJob
  id
  source_run_id
  kind
  state
  priority
  payload
  metadata
  attempts
  max_attempts
  available_at
  leased_at
  lease_expires_at
  locked_by
  created_at
  updated_at
  started_at
  completed_at
  last_error
```

The queue table stores **what work should be done**, not the Python objects needed
to do it.

Good payload:

```json
{
  "post_ids": [1, 2, 3],
  "source_run_id": "q_01HXW6T2KZ9A"
}
```

Bad payload:

```python
{
    "client": e621,
    "store": storage,
    "collection": e621.comments.search(post_id=123),
}
```

The queue is designed to survive process restarts. If six2one stops halfway
through a run, the next runner can reclaim pending/retryable jobs from SQLite and
continue.

---

## Job payloads must be serializable

All queued payloads and metadata must be JSON-serializable.

This is non-negotiable because jobs may be enqueued now and run later, possibly
in a different process.

Allowed:

```python
queue.enqueue(
    "download_image",
    {
        "post_id": 6407238,
        "file_url": "https://static1.e621.net/data/...",
        "destination": "~/.six2one/images/b7/5e/file.png",
        "expected_md5": "b75e9d31b415e590b5997ac4ca30c2a4",
    },
)
```

Not allowed:

```python
queue.enqueue(
    "download_image",
    {
        "post": post,             # model object
        "client": e621,           # API client
        "path": Path("image.png") # not plain JSON
    },
)
```

The queue API should validate this before writing a job:

```python
queue.enqueue("enrich_notes", {"post_ids": [123]})
```

If the payload cannot be encoded as JSON, enqueueing should fail immediately with
`QueuePayloadError`.

---

## Runtime context

`JobContext` contains the non-serializable services required to execute a job.

Example:

```python
@dataclass(frozen=True, slots=True)
class JobContext:
    storage: Storage
    e621: E621Client | None = None
    tags: object | None = None
    settings: object | None = None
    logger: object | None = None
```

The queue row says:

```text
Run enrich_notes for post IDs [1, 2, 3].
```

The context supplies:

```text
Use this Storage instance.
Use this E621Client instance.
Use this logger.
Use these runtime settings.
```

So this is correct inside a job:

```python
notes = context.e621.notes.search(post_id=post_id).all()
context.storage.notes.upsert_many(notes)
```

because `context.e621` is runtime infrastructure, not serialized queue payload.

---

## Queue

`Queue` is the high-level API used by application code.

```python
queue = Queue(storage=storage, registry=default_registry())
```

Typical operations:

```python
queue.enqueue("enrich_comments", {"post_ids": [123]})
queue.list()
queue.cancel(job_id)
queue.run_once(context)
queue.run_until_empty(context)
```

The queue object knows about:

- the storage facade;
- the job registry;
- payload serialization validation;
- job kind validation;
- optional job-level payload validation.

It does **not** know SQL.

Persistence is delegated to:

```python
storage.queue.enqueue(...)
storage.queue.claim_next(...)
storage.queue.complete(...)
storage.queue.fail(...)
storage.queue.cancel(...)
```

### Enqueue

```python
queue.enqueue(
    kind="enrich_comments",
    payload={
        "post_ids": [6407238],
        "source_run_id": "q_01HXW6T2KZ9A",
    },
    priority=10,
)
```

Returns a typed `QueueJob` row model.

### List

```python
pending = queue.list(states=["pending", "retrying"])
all_jobs = queue.list()
```

### Cancel

```python
queue.cancel(job_id)
```

Cancellation should be durable. A cancelled job should not be claimed by a runner.

---

## Job

A job class defines one executable unit of work.

```python
class Job:
    kind: str
    title: str
    description: str = ""
    max_attempts: int = 3

    def display(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {}

    def validate_payload(self, payload: Mapping[str, Any]) -> None:
        ...

    def run(self, context: JobContext, **payload: Any) -> JobResult:
        raise NotImplementedError
```

A job receives deserialized payload keyword arguments:

```python
job.run(
    context,
    post_ids=[6407238],
    source_run_id="q_01HXW6T2KZ9A",
)
```

A job does **not** receive the raw queue row unless it explicitly needs it.

### JobResult

Jobs return a `JobResult`.

```python
@dataclass(frozen=True, slots=True)
class JobResult:
    completed: bool = True
    message: str | None = None
    metadata: Mapping[str, Any] | None = None
    enqueue: tuple[NewJob, ...] = ()
```

Example:

```python
return JobResult(
    message="Cached comments for 12 posts",
    metadata={
        "posts": 12,
        "comments": 43,
    },
)
```

Jobs may request follow-up jobs:

```python
return JobResult(
    enqueue=(
        NewJob(
            kind="download_image",
            payload={
                "post_id": post.id,
                "file_url": post.file.url,
                "destination": destination,
            },
        ),
    ),
)
```

The runner persists those follow-up jobs after the current job succeeds.

---

## Registry

`JobRegistry` maps durable `kind` strings to executable job classes.

```python
registry = JobRegistry()
registry.register(EnrichCommentsJob)
registry.register(DownloadImageJob)

job = registry.create("enrich_comments")
```

A default registry is provided:

```python
from six2one.queue import default_registry

registry = default_registry()
```

The registry is intentionally explicit. It does not auto-import every module in
`jobs/`, because hidden discovery tends to make tests and packaging more brittle.

---

## Runner

`QueueRunner` claims pending jobs, executes them, and records state transitions.

```python
runner = QueueRunner(
    queue=queue,
    context=context,
    worker_id="worker-1",
)

runner.run_once()
runner.run_until_empty()
```

The runner flow:

```text
claim next pending/retryable job
  ↓
create job from registry
  ↓
run job with JobContext and payload kwargs
  ↓
if success:
    enqueue follow-up jobs
    mark complete
if failure:
    record error
    retry or fail permanently
```

The runner should only claim jobs that are available:

```text
state in pending/retrying
available_at <= now
not currently leased
```

The storage layer owns the exact SQL and leasing mechanics.

---

## Built-in jobs

The queue package provides job classes for the six2one pipeline.

### Core pipeline

| Job kind | Purpose |
|---|---|
| `fetch_page` | Fetch one page of posts from e621 and cache the post JSON. |
| `evaluate_query` | Evaluate a compiled/local query against cached posts and sidecars. |
| `download_image` | Download one image/media file for a final matched post. |

### Tag/database lifecycle

| Job kind | Purpose |
|---|---|
| `refresh_tag_database` | Refresh imported tag exports where the higher layer requests it. |

### Enrichment

| Job kind | Purpose |
|---|---|
| `enrich_users` | Hydrate users by ID/name. |
| `enrich_comments` | Fetch comments for post IDs. |
| `enrich_notes` | Fetch notes for post IDs. |
| `enrich_note_versions` | Fetch note version/update metadata. |
| `enrich_post_flags` | Fetch post flags/deletion reason metadata. |
| `enrich_post_events` | Fetch post event history. |
| `enrich_post_versions` | Fetch post version/edit history. |
| `enrich_post_approvals` | Fetch approval rows. |
| `enrich_pools` | Fetch pool metadata. |
| `enrich_sets` | Fetch visible post set metadata/membership through public APIs. |
| `enrich_replacements` | Fetch post replacement rows. |
| `enrich_favorites` | Fetch favorites where permitted. |
| `enrich_post_votes` | Fetch post vote rows where permitted; Moderator+ on e621. |
| `enrich_artists` | Fetch artist records. |
| `enrich_artist_urls` | Fetch artist URL records. |
| `enrich_artist_versions` | Fetch artist version records. |

The built-in jobs should follow the API distinctions from `six2one.e621`:

- deleted posts are discovered with `posts.search("status:deleted ...")`, not
  `/deleted_posts.json`;
- direct post vote rows are permission-sensitive / Moderator+;
- viewer vote discovery uses post search metatags such as `votedup:me`;
- public post-set membership should not assume `/post_sets/{id}/post_list.json`.

---

## Job state lifecycle

Jobs move through a small finite state machine.

```text
pending
  ↓ claim
running
  ↓ success
completed

running
  ↓ retryable failure
retrying
  ↓ available_at reached
pending/running again

running
  ↓ max attempts exceeded
failed

pending/running/retrying
  ↓ cancel
cancelled
```

Suggested states:

```python
class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

`BLOCKED` is useful for jobs such as `evaluate_query` that should wait until
required enrichment coverage exists.

---

## Storage boundary

The queue package uses storage, but does not contain SQL.

```text
queue.Queue
  validates job kind and payload
  calls storage.queue

queue.Runner
  claims jobs through storage.queue
  executes job classes
  writes completion/failure through storage.queue

storage.stores.queue.QueueStore
  owns SQL
  persists queue rows
  updates job states
```

This means multiple parts of the application can interact with queued work
through the same storage interface:

```python
storage.queue.list()
storage.queue.cancel(job_id)
storage.queue.events(job_id)
```

while the queue package remains responsible for execution semantics.

---

## Writing a custom job

A custom job needs:

1. a unique `kind`;
2. a JSON-serializable payload;
3. optional payload validation;
4. a `run()` method;
5. registration in the `JobRegistry`.

```python
from six2one.queue import Job, JobResult, JobContext


class EnrichExampleJob(Job):
    kind = "enrich_example"
    title = "Enrich example data"
    description = "Fetches example sidecar data for posts."

    def validate_payload(self, payload):
        if "post_ids" not in payload:
            raise ValueError("enrich_example requires post_ids")

    def display(self, payload):
        return {
            "Posts": len(payload.get("post_ids", [])),
        }

    def run(
        self,
        context: JobContext,
        *,
        post_ids: list[int],
        source_run_id: str | None = None,
    ) -> JobResult:
        # Use runtime services from context.
        rows = []
        for post_id in post_ids:
            rows.extend(context.e621.example.search(post_id=post_id).all())

        with context.storage.transaction():
            context.storage.example.upsert_many(rows)
            context.storage.enrichment.mark_ready(
                scope="post",
                keys=post_ids,
                dependency="ExampleIndex",
                source_run_id=source_run_id,
            )

        return JobResult(
            message=f"Enriched {len(post_ids)} posts",
            metadata={"posts": len(post_ids), "rows": len(rows)},
        )
```

Register it:

```python
registry.register(EnrichExampleJob)
```

Enqueue it:

```python
queue.enqueue(
    "enrich_example",
    {
        "post_ids": [1, 2, 3],
        "source_run_id": "q_...",
    },
)
```

---

## Displaying jobs in the CLI

Jobs expose display metadata so `621 queue list` and `621 queue show` do not need
to know every payload shape.

```python
class DownloadImageJob(Job):
    kind = "download_image"
    title = "Download image"

    def display(self, payload):
        return {
            "Post ID": payload["post_id"],
            "File": payload.get("filename", ""),
        }
```

The CLI can combine:

```text
QueueJob row fields
  state
  attempts
  timestamps
  last_error

Job display fields
  title
  payload-specific labels
```

Example:

```text
1. Download image
   id          j_01HXW...
   state       pending
   post id     6407238
   file        b75e9d31b415e590b5997ac4ca30c2a4.png
   attempts    0 / 3
```

---

## Retries and idempotency

Every job should be safe to retry.

That means jobs should use upserts, existence checks, and durable coverage marks.

Examples:

| Job | Idempotent behavior |
|---|---|
| `fetch_page` | Upsert posts; do not duplicate post rows. |
| `enrich_comments` | Upsert comments; mark `CommentsIndex` coverage ready. |
| `download_image` | If the file exists and checksum matches, mark complete. |
| `evaluate_query` | Recompute final matches for the source run. |

A job should not assume it is running for the first time. Power loss, process
kills, rate limits, and network errors can all produce retries.

---

## Package layout

```text
src/six2one/queue/
├── __init__.py
├── queue.py
├── job.py
├── registry.py
├── runner.py
├── models.py
├── errors.py
└── jobs/
    ├── __init__.py
    ├── fetch_page.py
    ├── evaluate_query.py
    ├── download_image.py
    ├── enrich_comments.py
    ├── enrich_notes.py
    ├── enrich_note_versions.py
    ├── enrich_moderation.py
    ├── enrich_pools.py
    ├── enrich_sets.py
    ├── enrich_replacements.py
    ├── enrich_social.py
    ├── enrich_users.py
    └── enrich_artists.py
```

### File responsibilities

| File | Owns |
|---|---|
| `queue.py` | High-level `Queue` API. |
| `job.py` | `Job`, `JobContext`, `JobResult`, `NewJob`. |
| `registry.py` | `JobRegistry`, `default_registry()`. |
| `runner.py` | `QueueRunner`. |
| `models.py` | Queue-facing enums and lightweight DTOs. |
| `errors.py` | Queue exception hierarchy. |
| `jobs/` | Built-in job implementations grouped by domain. |

---

## What this package does not do

`six2one.queue` does not:

- own SQLite SQL directly;
- define storage schema or migrations;
- parse e621 queries;
- decide which enrichment a query needs;
- know how terminal output is rendered;
- know e621 endpoint details beyond what each job calls through `JobContext`;
- serialize Python objects into queue payloads;
- replace the storage layer.

The intended call chain is:

```python
compiled = language.compile(query)

missing = storage.enrichment.missing(
    post_ids=candidate_post_ids,
    dependencies=compiled.bound.data_dependencies,
)

for need in missing:
    queue.enqueue(
        dependency_to_job_kind(need.dependency),
        need.to_payload(),
    )

runner.run_until_empty()
```

The query package decides what data is needed. The storage package decides what
is already present. The queue package runs the missing work. Runtime services
arrive through `JobContext`.

That is the whole little engine:

```text
durable JSON payload
  +
registered job class
  +
runtime context
  =
restart-safe work
```
