"""Pipelines reporter caps: sides, links and report size (DSM2-531).

Covers TEST-doc T-7: 10k distinct topics through one process stay within the
window caps (256 sides, 1024 links), the report stays under 60 KiB, drops are
counted, and the interned tables keep memory bounded.
"""

import msgpack

from ddtrace.internal.datastreams import pipelines

from .test_pipelines import FakeClock


def test_t7_side_cap_with_10k_distinct_topics():
    reporter = pipelines.PipelinesReporter(clock=FakeClock())
    sides_before, links_before = pipelines.drop_counts()
    for i in range(10000):
        reporter.on_produce("kafka", "", "topic-%d" % i)

    # interned memory is bounded by the caps
    assert len(reporter._sides) == pipelines.MAX_SIDES
    assert len(reporter._links) <= pipelines.MAX_LINKS

    report = reporter.take_report("svc", "")
    assert len(report["sides"]) == pipelines.MAX_SIDES
    assert len(report["links"]) <= pipelines.MAX_LINKS
    packed = pipelines.packb(report)
    assert len(packed) <= pipelines.MAX_REPORT_BYTES

    # every distinct topic beyond the cap was counted as dropped, exactly once
    sides_dropped, links_dropped = pipelines.drop_counts()
    assert sides_dropped - sides_before == 10000 - pipelines.MAX_SIDES
    assert links_dropped - links_before == 0

    # the window was reset: the interned tables were returned
    assert reporter._sides == {}
    assert reporter._links == {}


def test_t7_link_cap_drops_counted():
    reporter = pipelines.PipelinesReporter(clock=FakeClock())
    sides_before, links_before = pipelines.drop_counts()
    # 20 consume sides x 60 shared produce sides = 1200 distinct links with 80 sides
    for i in range(20):
        reporter.set_current_consume("kafka", "", "in-%d" % i, "g")
        for j in range(60):
            reporter.on_produce("kafka", "", "out-%d" % j)

    assert len(reporter._sides) == 80
    assert len(reporter._links) == pipelines.MAX_LINKS

    report = reporter.take_report("svc", "")
    assert len(report["sides"]) == 80
    assert len(report["links"]) == pipelines.MAX_LINKS
    assert len(pipelines.packb(report)) <= pipelines.MAX_REPORT_BYTES

    sides_dropped, links_dropped = pipelines.drop_counts()
    assert sides_dropped - sides_before == 0
    assert links_dropped - links_before == 1200 - pipelines.MAX_LINKS


def test_t7_report_size_cap_drops_links_first_and_stays_deterministic():
    long_name = "t" * 400
    # 300 long-named topics: 256 sides recorded, and the packed report alone
    # exceeds 60 KiB, so the flush trims it (links first, then sides).
    reporter = pipelines.PipelinesReporter(clock=FakeClock())
    sides_before, links_before = pipelines.drop_counts()
    for i in range(300):
        reporter.set_current_consume("kafka", "", long_name + "-in-%d" % i, "g")
        reporter.on_produce("kafka", "", long_name + "-out-%d" % i)

    report = reporter.take_report("svc", "")
    packed = pipelines.packb(report)
    assert len(packed) <= pipelines.MAX_REPORT_BYTES

    sides_dropped, links_dropped = pipelines.drop_counts()
    assert sides_dropped - sides_before >= 300 - pipelines.MAX_SIDES
    assert links_dropped - links_before >= 1  # the size cap dropped links too
    assert len(report["sides"]) < pipelines.MAX_SIDES  # sides were trimmed too

    # the trim is deterministic: the same events produce the same bytes
    twin = pipelines.PipelinesReporter(clock=FakeClock())
    for i in range(300):
        twin.set_current_consume("kafka", "", long_name + "-in-%d" % i, "g")
        twin.on_produce("kafka", "", long_name + "-out-%d" % i)
    assert pipelines.packb(twin.take_report("svc", "")) == packed
    # and the pinned encoding still holds for the trimmed report
    assert packed == msgpack.packb(report, use_bin_type=True)
