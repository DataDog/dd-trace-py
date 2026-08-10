"""Test helpers for asserting against DataStreamsProcessor state.

DSM aggregates checkpoints into 10s wall-clock buckets keyed by
``now_ns - (now_ns % bucket_size_ns)`` (see
``ddtrace/internal/datastreams/processor.py``). Tests that produce + consume in
quick succession can straddle a 10s boundary, splitting state across two
buckets — so any assertion that indexes ``list(buckets.values())[0]`` will
flake.

These helpers iterate all buckets under ``processor._lock`` so the periodic
flusher can't mutate ``_buckets`` mid-read.
"""


def max_produce_offset(processor, key):
    """Max ``latest_produce_offsets[key]`` across all DSM buckets."""
    with processor._lock:
        return max(
            (bucket.latest_produce_offsets.get(key, 0) for bucket in processor._buckets.values()),
            default=0,
        )


def max_commit_offset(processor, key):
    """Max ``latest_commit_offsets[key]`` across all DSM buckets."""
    with processor._lock:
        return max(
            (bucket.latest_commit_offsets.get(key, 0) for bucket in processor._buckets.values()),
            default=0,
        )


def all_pathway_stat_keys(processor):
    """Set of all pathway_stats keys across DSM buckets."""
    with processor._lock:
        return {key for bucket in processor._buckets.values() for key in bucket.pathway_stats.keys()}


def pathway_stats_merged(processor):
    """Merged ``pathway_stats`` dict across all DSM buckets.

    Uses ``dict.update()`` semantics — for the same key in multiple buckets, the
    last bucket's value wins, which silently loses count/sketch data. Safe for
    tests that produce one event per checkpoint key. Tests with multiple events
    per key (e.g. multi-message getmany) must aggregate counts manually instead.
    """
    with processor._lock:
        merged = {}
        for bucket in processor._buckets.values():
            merged.update(bucket.pathway_stats)
        return merged
