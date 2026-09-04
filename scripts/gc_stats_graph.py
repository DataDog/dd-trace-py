#!/usr/bin/env python3
"""Shared helpers for the GCMonitor ``gc-stats.json`` first-order reference graph.

Schema v2 (produced by
``ddtrace/internal/datadog/profiling/dd_wrapper/src/gc_monitor_json.cpp``) no
longer materializes a reference tree. It emits the first-order type graph and
lets the consumer reconstruct any tree it wants:

    "tt":  ["type.name", ...]                 # type table (string table)
    "tc":  [instance_count, ...]              # parallel to tt
    "tsz": [total_shallow_bytes, ...]         # parallel to tt
    "g":   {"<holder_idx>": [[held_idx, ic, ts], ...], ...}   # adjacency
    "roots": [holder_idx, ...]                # ordered reconstruction roots

Both ``gc_stats_decode.py`` and ``gc_stats_report.py`` import this module so the
graph parsing and tree reconstruction live in one place.
"""

from __future__ import annotations

from collections import deque
from typing import Any


# Reconstruction bounds. The type graph is cyclic and dense, so an unbounded
# expansion is factorial in the worst case; the budget is applied per root so a
# dense hub type cannot starve the small application roots.
DEFAULT_MAX_DEPTH: int = 6
DEFAULT_MAX_NODES_PER_ROOT: int = 512
DEFAULT_MAX_TOTAL_NODES: int = 2_000_000


def is_graph_format(data: dict[str, Any]) -> bool:
    """True for a schema-v2 snapshot that carries a first-order reference graph."""
    return isinstance(data.get("g"), dict) and isinstance(data.get("roots"), list)


def type_namer(data: dict[str, Any]):
    """Return a function mapping a type index to its name (flagging unknowns)."""
    tt: list[str] = data.get("tt", [])
    n = len(tt)

    def name(idx: int) -> str:
        return tt[idx] if 0 <= idx < n else f"<type#{idx}>"

    return name


def adjacency_by_index(data: dict[str, Any]) -> dict[int, list[tuple[int, int, int]]]:
    """Parse ``g`` into ``{holder_idx: [(held_idx, ic, ts), ...]}``."""
    raw: dict[str, Any] = data.get("g") or {}
    adj: dict[int, list[tuple[int, int, int]]] = {}
    for holder, edges in raw.items():
        adj[int(holder)] = [(int(e[0]), int(e[1]), int(e[2])) for e in edges]
    return adj


def build_name_adjacency(data: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Return a name-keyed canonical-children map and the ordered root seed nodes.

    The canonical map matches ``gc_stats_decode.build_canonical_children`` shape
    ({holder_name: [{"t", "ic", "ts"}, ...]}) so the decoder's expansion machinery
    can consume it directly. Root seeds carry each root's own instance count (tc)
    and total shallow size (tsz).
    """
    name = type_namer(data)
    tc: list[int] = data.get("tc", [])
    tsz: list[int] = data.get("tsz", [])
    n_tc, n_tsz = len(tc), len(tsz)

    canonical: dict[str, list[dict[str, Any]]] = {}
    for holder, edges in adjacency_by_index(data).items():
        canonical[name(holder)] = [{"t": name(h), "ic": ic, "ts": ts} for (h, ic, ts) in edges]

    root_seeds: list[dict[str, Any]] = []
    for idx in data.get("roots", []):
        i = int(idx)
        root_seeds.append({"t": name(i), "ic": tc[i] if 0 <= i < n_tc else 0, "ts": tsz[i] if 0 <= i < n_tsz else 0})
    return canonical, root_seeds


def reconstruct_indexed_forest(
    data: dict[str, Any],
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes_per_root: int = DEFAULT_MAX_NODES_PER_ROOT,
    max_total_nodes: int = DEFAULT_MAX_TOTAL_NODES,
) -> list[dict[str, Any]]:
    """Rebuild a reference forest of index-typed nodes from the first-order graph.

    Node shape matches the legacy ``rt`` format ({"t": type_idx, "ic", "ts",
    "ch"}) so existing index-based consumers work unchanged. A root's ic/ts come
    from tc/tsz; a child's ic/ts are the edge weights. Expansion is level-order
    (BFS) with a per-path cycle guard (``"cyc": true``) and per-root/depth/global
    caps (``"cut": true``).
    """
    adj = adjacency_by_index(data)
    tc: list[int] = data.get("tc", [])
    tsz: list[int] = data.get("tsz", [])
    n_tc, n_tsz = len(tc), len(tsz)
    total = 0

    def build_root(root_idx: int) -> dict[str, Any]:
        nonlocal total
        node: dict[str, Any] = {
            "t": root_idx,
            "ic": tc[root_idx] if 0 <= root_idx < n_tc else 0,
            "ts": tsz[root_idx] if 0 <= root_idx < n_tsz else 0,
        }
        total += 1
        used = 1
        queue: deque[tuple[dict[str, Any], int, frozenset[int]]] = deque([(node, 0, frozenset())])
        while queue:
            cur, depth, path = queue.popleft()
            edges = adj.get(cur["t"])
            if not edges or depth >= max_depth:
                continue
            if used >= max_nodes_per_root or total >= max_total_nodes:
                cur["cut"] = True
                continue
            child_path = path | {cur["t"]}
            children: list[dict[str, Any]] = []
            for held, ic, ts in edges:
                if used >= max_nodes_per_root or total >= max_total_nodes:
                    cur["cut"] = True
                    break
                total += 1
                used += 1
                child: dict[str, Any] = {"t": held, "ic": ic, "ts": ts}
                children.append(child)
                if held in child_path:
                    child["cyc"] = True
                else:
                    queue.append((child, depth + 1, child_path))
            cur["ch"] = children
        return node

    return [build_root(int(idx)) for idx in data.get("roots", [])]
