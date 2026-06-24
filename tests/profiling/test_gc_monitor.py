"""Tests for the GC reference-tree monitor (GCMonitor).

All tests run in subprocess isolation (separate Python process) so that
ddup/profiler state from one test does not bleed into another.

Timing model for each test:
  DD_PROFILING_GC_INTERVAL=3   -> snapshot taken every 3 seconds
  DD_PROFILING_GC_SURVIVOR_THRESHOLD=1 -> object flagged after surviving 1 consecutive snapshot
    (i.e. appears in snapshot N and snapshot N+1)
  time.sleep(8) -> ensures two snapshots run (t≈3 and t≈6) before p.stop()
  p.stop(flush=True) -> triggers a final upload that writes the gc-stats.json file
"""

import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_gc_monitor_detects_leak",
        DD_PROFILING_GC_INTERVAL="3",
        DD_PROFILING_GC_SURVIVOR_THRESHOLD="1",
    ),
    timeout=30,
)
def test_gc_monitor_detects_leak() -> None:
    """GC monitor writes a valid gc-stats.json that includes a deliberately leaked type.

    We leak 100 instances of a list subclass, each holding 500 integer elements.
    sys.getsizeof of each instance is ≈ 4056 bytes, which is larger than virtually
    any normal Python runtime object. This ensures the leaked instances rank near
    the top of the size-sorted suspect list and appear in the default top-N roots.
    """
    import glob
    import json
    import os
    import time

    from ddtrace.profiling.profiler import Profiler

    class _LeakCandidate(list):
        """Large list subclass: sys.getsizeof ≈ 4056 bytes with 500 elements.

        Subclassing list guarantees:
          - Objects are GC-tracked (list implements tp_traverse).
          - Shallow size (sys.getsizeof) equals the list buffer size, not just
            a pointer-sized instance, making these objects rank high by size.
        """

    # Module-level reference keeps all instances alive across GC snapshots.
    _leaked = [_LeakCandidate(range(500)) for _ in range(100)]  # noqa: F841

    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_prefix = pprof_prefix + "." + str(os.getpid())

    p = Profiler()
    p.start()

    # Timeline:
    #   t ≈ 3s: GC snapshot 1 -- _LeakCandidate instances are new, no suspects yet
    #   t ≈ 6s: GC snapshot 2 -- instances survived -> survivor_count 1 >= threshold 1
    #   t = 8s: p.stop(flush=True) writes gc-stats.json with the snapshot 2 data
    time.sleep(8)

    p.stop()  # flush=True by default

    # -- Locate the gc-stats output file --
    gc_files = sorted(glob.glob(output_prefix + ".*.gc-stats.json"))
    assert len(gc_files) > 0, f"No gc-stats.json files found matching {output_prefix}.*.gc-stats.json"

    with open(gc_files[-1]) as f:
        data = json.load(f)

    # -- Top-level schema --
    assert data["v"] == 1, f"Expected format version 1, got {data['v']}"
    assert isinstance(data["ts_ns"], int) and data["ts_ns"] > 0, "ts_ns must be a positive integer"
    assert "gc" in data, "Expected 'gc' block in output"
    assert "tt" in data, "Expected 'tt' type table in output"
    assert "r" in data, "Expected 'r' root array in output"

    # -- GC engine stats block --
    gc_block = data["gc"]
    assert gc_block["enabled"] is True, "GC must be enabled during test"
    assert len(gc_block["thresholds"]) == 3, "Expected three GC generation thresholds"
    assert all(t > 0 for t in gc_block["thresholds"]), "All thresholds must be positive"
    assert len(gc_block["gen"]) == 3, "Expected stats for three GC generations"
    assert len(gc_block["d_gen"]) == 3, "Expected deltas for three GC generations"
    for gen_entry in gc_block["gen"]:
        assert "n" in gen_entry and "col" in gen_entry and "uncol" in gen_entry, (
            f"Generation entry missing required keys: {gen_entry}"
        )
    assert gc_block["garbage"] >= 0, "garbage count must be non-negative"

    # -- Type table --
    # The type table is built by scanning all GC-tracked objects, so our
    # _LeakCandidate type appears there regardless of top-N suspect filtering.
    assert len(data["tt"]) > 0, "Type table must not be empty"
    leaked_type = next((t for t in data["tt"] if "_LeakCandidate" in t), None)
    assert leaked_type is not None, (
        "Expected _LeakCandidate in type table. "
        f"Non-builtins types: {[t for t in data['tt'] if not t.startswith('builtins')][:20]}"
    )

    # -- Reference tree root nodes --
    assert len(data["r"]) > 0, "Expected at least one root node (suspect objects)"

    # Each _LeakCandidate(range(500)) has sys.getsizeof ≈ 4056 bytes -- larger
    # than virtually any normal Python runtime object -- so these instances rank
    # near the top of the size-sorted suspect list and must appear in root nodes.
    leaked_idx = data["tt"].index(leaked_type)
    leaked_roots = [r for r in data["r"] if r["t"] == leaked_idx]
    assert len(leaked_roots) > 0, (
        f"Expected {leaked_type!r} in root nodes. Root types found: {[data['tt'][r['t']] for r in data['r']]}"
    )

    leaked_root = leaked_roots[0]
    assert leaked_root["ic"] > 0, "Instance count must be positive"
    assert leaked_root["ts"] > 0, "Total size must be positive"
    # Without DD_PROFILING_GC_REFERRERS=true, the referrer walk is skipped and
    # the root category is always '?'
    assert leaked_root["c"] == "?", f"Expected root category '?' (referrers disabled), got {leaked_root['c']!r}"
    # 'fn' is only populated for K/S roots identified via the referrer walk
    assert "fn" not in leaked_root, "'fn' should not be present when referrers are disabled"


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_gc_monitor_profiler_stats",
        DD_PROFILING_GC_INTERVAL="3",
        DD_PROFILING_GC_SURVIVOR_THRESHOLD="1",
    ),
    timeout=30,
)
def test_gc_monitor_profiler_stats() -> None:
    """GC snapshot timing metrics are emitted in the internal_metadata.json sidecar.

    The ProfilerStats integration must accumulate at least one snapshot's timing
    data and surface it in the metadata file that accompanies each uploaded profile.
    """
    import glob
    import json
    import os
    import time

    from ddtrace.profiling.profiler import Profiler

    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_prefix = pprof_prefix + "." + str(os.getpid())

    p = Profiler()
    p.start()
    time.sleep(8)  # allow ≥ 2 snapshots at 3s interval
    p.stop()

    meta_files = sorted(glob.glob(output_prefix + ".*.internal_metadata.json"))
    assert len(meta_files) > 0, f"No internal_metadata.json files matching {output_prefix}.*.internal_metadata.json"

    with open(meta_files[-1]) as f:
        meta = json.load(f)

    # At least one GC snapshot must have completed before the flush
    assert "gc_snapshot_count" in meta, f"gc_snapshot_count missing; keys: {list(meta.keys())}"
    assert meta["gc_snapshot_count"] >= 1, f"Expected at least 1 snapshot, got {meta['gc_snapshot_count']}"

    # Total wall time must be positive
    assert "gc_snapshot_wall_time_us" in meta
    assert meta["gc_snapshot_wall_time_us"] > 0, "gc_snapshot_wall_time_us must be positive"

    # All per-step timing fields must be present and non-negative
    step_keys = (
        "gc_gc_stats_time_us",
        "gc_get_objects_time_us",
        "gc_type_scan_time_us",
        "gc_survivor_update_time_us",
        "gc_referrers_time_us",
        "gc_serialize_time_us",
    )
    for key in step_keys:
        assert key in meta, f"{key!r} missing from metadata"
        assert meta[key] >= 0, f"{key} must be non-negative, got {meta[key]}"

    # Steps that do real work every snapshot must have non-zero cumulative time
    assert meta["gc_gc_stats_time_us"] > 0, "GC stats query time must be positive"
    assert meta["gc_get_objects_time_us"] > 0, "gc.get_objects() time must be positive"
    assert meta["gc_type_scan_time_us"] > 0, "Type scan time must be positive"
    assert meta["gc_serialize_time_us"] > 0, "Serialization time must be positive"

    # The wall time should be at least the sum of the individual step times
    step_sum = sum(meta[k] for k in step_keys)
    assert meta["gc_snapshot_wall_time_us"] >= step_sum, (
        f"Wall time {meta['gc_snapshot_wall_time_us']}us < sum of steps {step_sum}us"
    )


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_gc_monitor_disabled",
        DD_PROFILING_GC_ENABLED="false",
    ),
    timeout=20,
)
def test_gc_monitor_disabled() -> None:
    """When DD_PROFILING_GC_ENABLED=false no gc-stats.json file is written."""
    import glob
    import os
    import time

    from ddtrace.profiling.profiler import Profiler

    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_prefix = pprof_prefix + "." + str(os.getpid())

    p = Profiler()
    p.start()
    time.sleep(3)
    p.stop()

    gc_files = glob.glob(output_prefix + ".*.gc-stats.json")
    assert len(gc_files) == 0, f"Expected no gc-stats.json when GC monitor is disabled, found: {gc_files}"

    # A regular pprof + internal_metadata.json should still exist (profiler works normally)
    pprof_files = glob.glob(output_prefix + ".*.pprof")
    assert len(pprof_files) > 0, "Expected pprof output even when GC monitor is disabled"


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_gc_monitor_schema",
        DD_PROFILING_GC_INTERVAL="3",
        DD_PROFILING_GC_SURVIVOR_THRESHOLD="1",
    ),
    timeout=30,
)
def test_gc_monitor_json_schema() -> None:
    """Validate every field of the gc-stats.json schema precisely.

    Checks types, ranges, and internal consistency (e.g. cumulative gen stats
    are non-negative; deltas do not exceed cumulative values).
    """
    import glob
    import json
    import os
    import time

    from ddtrace.profiling.profiler import Profiler

    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_prefix = pprof_prefix + "." + str(os.getpid())

    p = Profiler()
    p.start()
    time.sleep(8)
    p.stop()

    gc_files = sorted(glob.glob(output_prefix + ".*.gc-stats.json"))
    assert len(gc_files) > 0, f"No gc-stats.json files found under {output_prefix}"

    with open(gc_files[-1]) as f:
        data = json.load(f)

    # v
    assert isinstance(data["v"], int) and data["v"] == 1

    # ts_ns
    assert isinstance(data["ts_ns"], int) and data["ts_ns"] > 0

    # gc block
    gc_block = data["gc"]
    assert isinstance(gc_block["enabled"], bool)
    assert isinstance(gc_block["thresholds"], list) and len(gc_block["thresholds"]) == 3
    assert all(isinstance(t, int) and t >= 0 for t in gc_block["thresholds"])
    assert isinstance(gc_block["garbage"], int) and gc_block["garbage"] >= 0

    for label, generations in (("gen", gc_block["gen"]), ("d_gen", gc_block["d_gen"])):
        assert len(generations) == 3, f"{label} must have 3 entries"
        for g in generations:
            for field in ("n", "col", "uncol"):
                assert field in g, f"Field {field!r} missing from {label} entry"
                assert isinstance(g[field], int) and g[field] >= 0, (
                    f"{label}.{field} must be a non-negative int, got {g[field]!r}"
                )

    # Cumulative counts must be >= deltas (deltas can't exceed what was measured)
    for i in range(3):
        for field in ("n", "col", "uncol"):
            assert gc_block["d_gen"][i][field] <= gc_block["gen"][i][field], (
                f"d_gen[{i}].{field} ({gc_block['d_gen'][i][field]}) > gen[{i}].{field} ({gc_block['gen'][i][field]})"
            )

    # tt -- type table
    assert isinstance(data["tt"], list)
    assert all(isinstance(t, str) and len(t) > 0 for t in data["tt"]), "All entries in tt must be non-empty strings"
    # Type names must be unique
    assert len(data["tt"]) == len(set(data["tt"])), "Type table must not contain duplicates"

    # r -- root nodes
    assert isinstance(data["r"], list)
    valid_categories = {"K", "S", "F", "O", "?"}
    for root in data["r"]:
        assert isinstance(root["t"], int) and 0 <= root["t"] < len(data["tt"]), (
            f"Root type index {root['t']} out of range for tt (len={len(data['tt'])})"
        )
        assert root["c"] in valid_categories, f"Root category {root['c']!r} not in {valid_categories}"
        assert isinstance(root["ic"], int) and root["ic"] > 0, "ic must be a positive int"
        assert isinstance(root["ts"], int) and root["ts"] > 0, "ts must be a positive int"
        if "fn" in root:
            assert isinstance(root["fn"], str) and len(root["fn"]) > 0
        if "ch" in root:
            assert isinstance(root["ch"], list)
            for child in root["ch"]:
                assert isinstance(child["t"], int) and 0 <= child["t"] < len(data["tt"])
                assert isinstance(child["ic"], int) and child["ic"] > 0
                assert isinstance(child["ts"], int) and child["ts"] > 0
