from ddtrace.internal.processor.stats import DDStatsCollector


def test_stats_counts():
    stats = DDStatsCollector()
    counts = stats.pop_counts()
    assert counts == []

    stats.increment("myop.hits", tags=["http.status_code:200", "service:svc1"])
    stats.increment("myop.hits", tags=["http.status_code:200", "service:svc1"])
    stats.increment("myop.hits", tags=["http.status_code:200", "service:svc2"])
    stats.increment("myop.hits", tags=["http.status_code:500", "service:svc1"])
    counts = stats.pop_counts()
    assert counts == [
        (("myop.hits", "http.status_code:200", "service:svc1"), 2),
        (("myop.hits", "http.status_code:200", "service:svc2"), 1),
        (("myop.hits", "http.status_code:500", "service:svc1"), 1),
    ]


def test_stats_distributions():
    stats = DDStatsCollector()
    dists = stats.pop_distributions()
    assert dists == []

    stats.distribution("myop.duration", 100, tags=["http.status_code:200", "service:svc1"])
    stats.distribution("myop.duration", 200, tags=["http.status_code:200", "service:svc1"])
    stats.distribution("myop.duration", 125, tags=["http.status_code:200", "service:svc1"])
    stats.distribution("myop.duration", 125, tags=["http.status_code:200", "service:svc2"])
    stats.distribution("myop.duration", 120, tags=["http.status_code:200", "service:svc2"])
    dists = stats.pop_distributions()
    assert len(dists) == 2
    key, dist = dists[0]
    assert dist.count == 3
    assert dist.avg == (100 + 200 + 125) / 3
    dists = stats.pop_distributions()
    assert dists == []
