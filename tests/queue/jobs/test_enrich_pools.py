from __future__ import annotations

from six2one.queue import JobContext
from six2one.queue.jobs.enrich_pools import EnrichPoolsJob
from tests.factories import FakeE621
from tests.support import import_test_posts, make_post


def test_enrich_pools_fetches_membership_by_post_id_and_marks_coverage_ready(store):
    run = store.source_runs.start(query="pool:fox_and_the_grapes")
    import_test_posts(store, make_post(1))
    context = JobContext(store=store, e621=FakeE621())

    result = EnrichPoolsJob().run(context, post_ids=[1], source_run_id=int(run.id))

    assert result.metadata["pools"] == 1
    assert store.pools.for_post(1)[0].name == "fox_and_the_grapes"
    assert store.coverage.missing_post_ids(post_ids=(1,), dependency="PoolIndex") == ()
