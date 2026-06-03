"""Generate a reference graph of all GC-tracked objects in memory.

Output is a JSON file with:
  - "meta": build timings and counts
  - "type_registry": {type_id -> type_name}
  - "nodes": {obj_id -> {"type_id": ..., "generation": 0|1|2, "repr": ...}}
  - "edges": [[src_id, dst_id], ...]
"""

import gc
import json
import sys
import time
from typing import Any


def _safe_repr(obj: Any, max_len: int = 80) -> str:
    try:
        r = repr(obj)
    except Exception:
        r = "<repr error>"
    return r[:max_len]


def build_gc_graph() -> dict[str, Any]:
    t_start = time.perf_counter()

    t_collect = time.perf_counter()
    gc.collect()
    t_collect_ms = (time.perf_counter() - t_collect) * 1000

    t_get_objects = time.perf_counter()
    objects = gc.get_objects()
    t_get_objects_ms = (time.perf_counter() - t_get_objects) * 1000

    # type_registry: id(type) -> qualified name (deduped)
    type_registry: dict[int, str] = {}

    def _register_type(t: type) -> int:
        tid = id(t)
        if tid not in type_registry:
            type_registry[tid] = f"{t.__module__}.{t.__qualname__}"
        return tid

    # nodes: obj_id -> {type_id, repr}
    nodes: dict[int, dict[str, Any]] = {}
    # edges: list of [src_id, dst_id]
    edges: list[list[int]] = []

    # Build a generation lookup: obj_id -> 0 | 1 | 2
    t_gen_lookup = time.perf_counter()
    obj_generation: dict[int, int] = {}
    for gen in (0, 1, 2):
        for o in gc.get_objects(generation=gen):
            obj_generation[id(o)] = gen
    t_gen_lookup_ms = (time.perf_counter() - t_gen_lookup) * 1000

    obj_id_set: set[int] = {id(o) for o in objects}

    t_traverse = time.perf_counter()
    for obj in objects:
        oid = id(obj)
        t = type(obj)
        tid = _register_type(t)
        nodes[oid] = {
            "type_id": tid,
            "generation": obj_generation.get(oid),
            "repr": _safe_repr(obj),
        }

        try:
            referents = gc.get_referents(obj)
        except Exception:
            referents = []

        for ref in referents:
            rid = id(ref)
            if rid in obj_id_set:
                edges.append([oid, rid])
    t_traverse_ms = (time.perf_counter() - t_traverse) * 1000

    t_total_ms = (time.perf_counter() - t_start) * 1000

    return {
        "meta": {
            "total_ms": round(t_total_ms, 3),
            "gc_collect_ms": round(t_collect_ms, 3),
            "get_objects_ms": round(t_get_objects_ms, 3),
            "gen_lookup_ms": round(t_gen_lookup_ms, 3),
            "traverse_ms": round(t_traverse_ms, 3),
            "n_nodes": len(nodes),
            "n_edges": len(edges),
            "n_types": len(type_registry),
            "n_gc_tracked": sum(gc.get_count()),
            "n_gc_tracked_by_gen": dict(zip((0, 1, 2), gc.get_count())),
        },
        "type_registry": {str(k): v for k, v in type_registry.items()},
        "nodes": {str(k): v for k, v in nodes.items()},
        "edges": [[str(s), str(d)] for s, d in edges],
    }


def main() -> None:
    output_path = sys.argv[1] if len(sys.argv) > 1 else "gc_graph.json"
    graph = build_gc_graph()

    m = graph["meta"]
    print(
        f"nodes={m['n_nodes']}  edges={m['n_edges']}  distinct_types={m['n_types']}  "
        f"total={m['total_ms']}ms  "
        f"(collect={m['gc_collect_ms']}ms  get_objects={m['get_objects_ms']}ms  "
        f"gen_lookup={m['gen_lookup_ms']}ms  traverse={m['traverse_ms']}ms)"
    )

    with open(output_path, "w") as f:
        json.dump(graph, f, indent=2)
    print(f"written to {output_path}")


if __name__ == "__main__":
    main()
