"""Tests for the GC reference-tree monitor (GCMonitor).

All tests run in subprocess isolation (separate Python process) so that
ddup/profiler state from one test does not bleed into another.

Timing model for each test:
  DD_PROFILING_GC_INTERVAL=3   -> snapshot taken every 3 seconds
  time.sleep(8) -> ensures two snapshots run (t≈3 and t≈6) before p.stop()
  p.stop(flush=True) -> triggers a final upload that writes the gc-stats.json file
"""

import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_gc_monitor_detects_leak",
        DD_PROFILING_GC_INTERVAL="3",
    ),
    timeout=30,
)
def test_gc_monitor_detects_leak() -> None:
    """GC monitor writes a valid gc-stats.json whose type histogram includes a leaked type."""
    import glob
    import json
    import os
    import time

    from ddtrace.profiling.profiler import Profiler

    class _LeakCandidate(list):
        """List subclass so instances are GC-tracked (list implements tp_traverse)."""

    # Module-level reference keeps all instances alive across GC snapshots.
    _leaked = [_LeakCandidate(range(500)) for _ in range(100)]  # noqa: F841

    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_prefix = pprof_prefix + "." + str(os.getpid())

    p = Profiler()
    p.start()
    time.sleep(8)
    p.stop()

    gc_files = sorted(glob.glob(output_prefix + ".*.gc-stats.json"))
    assert len(gc_files) > 0, f"No gc-stats.json files found matching {output_prefix}.*.gc-stats.json"

    with open(gc_files[-1]) as f:
        data = json.load(f)

    assert data["v"] == 1, f"Expected format version 1, got {data['v']}"
    assert isinstance(data["ts_ns"], int) and data["ts_ns"] > 0, "ts_ns must be a positive integer"
    assert "gc" in data, "Expected 'gc' block in output"
    assert "tt" in data, "Expected 'tt' type table in output"
    assert "tc" in data, "Expected 'tc' type counts array in output"

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

    assert len(data["tt"]) > 0, "Type table must not be empty"
    assert len(data["tt"]) == len(data["tc"]), "tt and tc must have the same length"
    leaked_type = next((t for t in data["tt"] if "_LeakCandidate" in t), None)
    assert leaked_type is not None, (
        "Expected _LeakCandidate in type table. "
        f"Non-builtins types: {[t for t in data['tt'] if not t.startswith('builtins')][:20]}"
    )

    # The leaked instances must appear in the histogram with the expected count.
    leaked_idx = data["tt"].index(leaked_type)
    assert data["tc"][leaked_idx] >= 100, (
        f"Expected >= 100 live _LeakCandidate instances in tc, got {data['tc'][leaked_idx]}"
    )


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
        "gc_name_resolve_time_us",
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

    assert isinstance(data["v"], int) and data["v"] == 1
    assert isinstance(data["ts_ns"], int) and data["ts_ns"] > 0

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
    assert len(data["tt"]) == len(set(data["tt"])), "Type table must not contain duplicates"

    # tc -- per-type instance counts (parallel array to tt)
    assert isinstance(data["tc"], list)
    assert len(data["tc"]) == len(data["tt"]), "tc and tt must have the same length"
    assert all(isinstance(c, int) and c > 0 for c in data["tc"]), "All entries in tc must be positive ints"

    # rt -- reference forest (empty when DD_PROFILING_GC_REFERRERS is not set)
    tt = data["tt"]

    def validate_rt_node(node) -> None:
        assert isinstance(node["t"], int) and 0 <= node["t"] < len(tt)
        assert isinstance(node["ic"], int) and node["ic"] > 0
        assert isinstance(node["ts"], int) and node["ts"] > 0
        if "ch" in node:
            assert isinstance(node["ch"], list)
            for child in node["ch"]:
                validate_rt_node(child)

    assert isinstance(data["rt"], list)
    for node in data["rt"]:
        validate_rt_node(node)


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_gc_monitor_reference_chains",
        DD_PROFILING_GC_INTERVAL="3",
        DD_PROFILING_GC_REFERRERS="1",
    ),
    timeout=30,
)
def test_gc_monitor_reference_chains_1() -> None:
    """With referrers enabled, the reference forest (rt) captures holder -> held edges."""
    from dataclasses import dataclass
    import glob
    import json
    import os
    import random
    import time

    from ddtrace.profiling.profiler import Profiler

    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_prefix = pprof_prefix + "." + str(os.getpid())

    @dataclass
    class Customer:
        first_name: str
        last_name: str

    @dataclass
    class Product:
        name: str
        price: float

    @dataclass
    class Order:
        customer: "Customer"
        products: "list[Product]"

    @dataclass
    class MyApp:
        orders: "list[Order]"
        customers: "list[Customer]"
        products: "list[Product]"

        def create_random_customers(self) -> None:
            self.customers.append(
                Customer(first_name=f"John {len(self.customers)}", last_name=f"Doe {len(self.customers)}")
            )

        def create_random_products(self) -> None:
            self.products.append(Product(name=f"Product {len(self.products)}", price=random.random()))

        def create_random_orders(self) -> None:
            self.orders.append(
                Order(
                    customer=self.customers[random.randint(0, len(self.customers) - 1)],
                    products=[self.products[random.randint(0, len(self.products) - 1)]],
                )
            )

    customers = [Customer(first_name="John", last_name="Doe"), Customer(first_name="Jane", last_name="Doe")]
    products = [Product(name="Product 1", price=10.0), Product(name="Product 2", price=20.0)]
    orders = [Order(customer=customers[0], products=products), Order(customer=customers[1], products=products)]

    app = MyApp(orders=orders, customers=customers, products=products)

    p = Profiler()
    p.start()
    for _ in range(15):
        app.create_random_customers()
        app.create_random_products()
        app.create_random_orders()
        time.sleep(1)
    p.stop()

    gc_files = sorted(glob.glob(output_prefix + ".*.gc-stats.json"))
    assert len(gc_files) > 0, f"No gc-stats.json files found under {output_prefix}"

    latest = gc_files[-1]
    with open(latest) as f:
        raw = f.read()

    data = json.loads(raw)

    tt = data["tt"]
    assert isinstance(data["rt"], list)
    assert len(data["rt"]) > 0, "Expected a non-empty reference forest when referrers are enabled"

    # MyApp holds Order, Customer, Product; Order holds Customer and Product.
    def find_root(type_name: str):
        for root in data["rt"]:
            if tt[root["t"]] == type_name:
                return root
        return None

    myapp_root = find_root("__main__.MyApp")
    assert myapp_root is not None, f"Expected __main__.MyApp in rt roots. Roots: {[tt[r['t']] for r in data['rt']]}"

    # MyApp root: exactly one live instance, non-zero retained bytes.
    assert myapp_root["ic"] == 1, f"Expected exactly one MyApp instance, got ic={myapp_root['ic']}"
    assert myapp_root["ts"] > 0, "MyApp root ts must be > 0"

    # MyApp holds Order, Customer, Product directly (via its three list fields).
    myapp_children = {tt[c["t"]]: c for c in myapp_root.get("ch", [])}
    for expected in ("__main__.Order", "__main__.Customer", "__main__.Product"):
        assert expected in myapp_children, (
            f"Expected {expected!r} as a direct child of MyApp; children: {sorted(myapp_children)}"
        )
        edge = myapp_children[expected]
        assert edge["ic"] > 0, f"Edge MyApp -> {expected} must aggregate at least one reference"
        assert edge["ts"] > 0, f"Edge MyApp -> {expected} must have a positive retained byte count"

    # We appended ~15 orders/customers/products in the loop plus the 2 seeded
    # each; every one is retained by MyApp, so the aggregated ref counts on the
    # collapsed edges must reflect that scale (not e.g. 1 or 2).
    assert myapp_children["__main__.Order"]["ic"] >= 15, (
        f"MyApp -> Order aggregated ic should be >= 15, got {myapp_children['__main__.Order']['ic']}"
    )
    assert myapp_children["__main__.Customer"]["ic"] >= 15, (
        f"MyApp -> Customer aggregated ic should be >= 15, got {myapp_children['__main__.Customer']['ic']}"
    )
    assert myapp_children["__main__.Product"]["ic"] >= 15, (
        f"MyApp -> Product aggregated ic should be >= 15, got {myapp_children['__main__.Product']['ic']}"
    )

    # Order retains Customer (via its customer field) and Product (via its
    # products list), so under MyApp -> Order we must also see those edges.
    order_edge = myapp_children["__main__.Order"]
    order_grandchildren = {tt[c["t"]] for c in order_edge.get("ch", [])}
    for expected in ("__main__.Customer", "__main__.Product"):
        assert expected in order_grandchildren, (
            f"Expected {expected!r} under MyApp -> Order; grandchildren: {sorted(order_grandchildren)}"
        )

    # Sanity: leaf application types (Customer, Product) should not have
    # application-type children hanging off them here -- they only reference
    # str/float, which the excluded-types filter or transparent-stepping
    # remove. Empty children is fine; nested __main__.* children would be
    # surprising for these dataclasses.
    for leaf_name in ("__main__.Customer", "__main__.Product"):
        leaf_edge = myapp_children[leaf_name]
        leaf_grandchildren = {tt[c["t"]] for c in leaf_edge.get("ch", [])}
        unexpected = {g for g in leaf_grandchildren if g.startswith("__main__.")}
        assert not unexpected, f"Unexpected __main__.* grandchildren under MyApp -> {leaf_name}: {unexpected}"

    # Same for the top-level Order root (if the forest exposes one).
    order_root = find_root("__main__.Order")
    if order_root is not None:
        assert order_root["ic"] >= 15, f"Order root ic should be >= 15, got {order_root['ic']}"
        top_order_children = {tt[c["t"]] for c in order_root.get("ch", [])}
        for expected in ("__main__.Customer", "__main__.Product"):
            assert expected in top_order_children, (
                f"Expected {expected!r} as a child of the Order root; children: {sorted(top_order_children)}"
            )


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_gc_monitor_reference_chains_2",
        DD_PROFILING_GC_INTERVAL="3",
        DD_PROFILING_GC_REFERRERS="1",
    ),
    timeout=180,
    out=None,  # allow the timing prints below
)
def test_gc_monitor_reference_chains_stress_test() -> None:
    """Stress test with ~10M objects (10K parents, each with 1000 children).

    Confirms the reference-chain walk still finishes -- and stays reasonably
    fast -- against a large, wide object graph, and that the resulting
    gc-stats.json still reflects the graph correctly.
    """
    from dataclasses import dataclass
    from dataclasses import field
    import glob
    import json
    import os
    import time

    from ddtrace.profiling.profiler import Profiler

    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_prefix = pprof_prefix + "." + str(os.getpid())

    NUM_PARENTS = 10_000
    CHILDREN_PER_PARENT = 1000
    TOTAL_OBJECTS = NUM_PARENTS * (1 + CHILDREN_PER_PARENT)  # ~10M

    @dataclass
    class Child:
        value: int

    @dataclass
    class Parent:
        children: list[Child] = field(default_factory=list)

    build_start = time.perf_counter()
    parents = [Parent(children=[Child(value=i) for i in range(CHILDREN_PER_PARENT)]) for _ in range(NUM_PARENTS)]
    build_time_s = time.perf_counter() - build_start
    print(
        f"Built {TOTAL_OBJECTS} objects ({NUM_PARENTS} parents x {CHILDREN_PER_PARENT} children) in {build_time_s:.2f}s"
    )

    p = Profiler()
    p.start()

    snapshot_start = time.perf_counter()
    time.sleep(8)  # allow >= 2 snapshots at 3s interval to walk the graph
    p.stop()
    snapshot_time_s = time.perf_counter() - snapshot_start
    print(f"Profiler ran for {snapshot_time_s:.2f}s while snapshotting the {TOTAL_OBJECTS}-object graph")

    # Keep the graph alive until after stop() so it is present for the final snapshot.
    assert len(parents) == NUM_PARENTS

    gc_files = sorted(glob.glob(output_prefix + ".*.gc-stats.json"))
    assert len(gc_files) > 0, f"No gc-stats.json files found under {output_prefix}"

    with open(gc_files[-1]) as f:
        data = json.load(f)

    # At this scale the object-graph walk's edge safety valve (a hard cap that
    # protects against pathologically large heaps) can legitimately truncate
    # the scan before every object is tallied, so a small undercount is
    # expected -- assert "most of" the instances were counted rather than an
    # exact match.
    MIN_FRACTION = 0.9

    tt = data["tt"]
    parent_idx = next((i for i, t in enumerate(tt) if t.endswith(".Parent")), None)
    assert parent_idx is not None, f"Expected Parent type in type table: {[t for t in tt if 'Parent' in t]}"
    assert data["tc"][parent_idx] >= NUM_PARENTS * MIN_FRACTION, (
        f"Expected >= {MIN_FRACTION:.0%} of {NUM_PARENTS} Parent instances, got {data['tc'][parent_idx]}"
    )

    child_idx = next((i for i, t in enumerate(tt) if t.endswith(".Child")), None)
    assert child_idx is not None, f"Expected Child type in type table: {[t for t in tt if 'Child' in t]}"
    expected_children = NUM_PARENTS * CHILDREN_PER_PARENT
    assert data["tc"][child_idx] >= expected_children * MIN_FRACTION, (
        f"Expected >= {MIN_FRACTION:.0%} of {expected_children} Child instances, got {data['tc'][child_idx]}"
    )

    meta_files = sorted(glob.glob(output_prefix + ".*.internal_metadata.json"))
    assert len(meta_files) > 0, f"No internal_metadata.json files matching {output_prefix}"
    with open(meta_files[-1]) as f:
        meta = json.load(f)

    wall_time_us = meta["gc_snapshot_wall_time_us"]
    print(f"GC snapshot wall time: {wall_time_us / 1000:.1f}ms for ~{TOTAL_OBJECTS} objects")

    # A large regression here (e.g. accidental O(n^2) behavior in the
    # reference-chain walk) would blow past this generous bound.
    max_wall_time_us = 60_000_000  # 60s
    assert wall_time_us < max_wall_time_us, (
        f"GC snapshot took {wall_time_us / 1e6:.1f}s for ~{TOTAL_OBJECTS} objects, "
        f"expected < {max_wall_time_us / 1e6:.0f}s"
    )
