"""Continuous GC leak monitor.

Runs a background thread that periodically snapshots all GC-tracked objects,
excludes everything that was already alive at a startup baseline (Python
internals, ddtrace internals, imported modules, ...), and tracks how long each
surviving object has been alive. One JSON file is written per snapshot listing
the leak candidates grouped by type and sorted by object age.

Usage::

    import gcmonitor
    mon = gcmonitor.start(interval=30)
    ...
    mon.stop()

Pass ``exclude_types`` to suppress types that should never count as leaks
(matched by full ``module.Qualname`` or short name). When omitted,
``DEFAULT_EXCLUDED_TYPES`` is used. Pass an empty iterable to disable filtering.

When ``reference_chains`` is enabled (default), each sampled candidate gets a
``reference_chain``: a best-effort walk of its referrers toward a GC root
(a module), explaining *why* the object is still alive. Each entry records the
holder's type/repr/id and ``via`` (how it references the previous object, e.g.
``dict key 'x'``, ``index 3``, ``attribute 'cache'``). This relies on
``gc.get_referrers``, which scans the whole heap per step, so it is the most
expensive part of a snapshot; disable it with ``reference_chains=False`` or
lower ``max_chain_depth`` for very large heaps.

Notes / caveats:
  - Object identity is tracked by ``id`` (memory address). A reused address is
    treated as a brand-new object, which is the correct behaviour for a leak
    heuristic (the previous occupant was collected).
  - No strong or weak references to traced objects are retained, so monitoring
    never keeps candidate objects alive.
  - ``gctest.py`` remains the one-shot full-graph dumper; this module is the
    continuous, baseline-filtered tracker. Its output is intentionally not
    gcview-compatible (candidates only, no edges).
"""

from __future__ import annotations

import gc
import json
import os
import threading
import time
from collections import defaultdict
from types import FrameType
from types import ModuleType
from typing import Any
from typing import Iterable


# Types that are almost always noise (mostly produced by the monitor thread and
# the interpreter itself) and should never be reported as leaks. Names may be
# fully qualified (``builtins.cell``) or short (``cell``).
DEFAULT_EXCLUDED_TYPES: frozenset[str] = frozenset(
    {
        "cell",
        "frame",
        "method",
        "builtin_function_or_method",
        "method-wrapper",
        "weakref",
        "ReferenceType",
        "builtins.method_descriptor",
        "builtins.getset_descriptor",
        "builtins.property",
    }
)


def _safe_repr(obj: Any, max_len: int = 80) -> str:
    try:
        r = repr(obj)
    except Exception:
        r = "<repr error>"
    return r[:max_len]


class _Track:
    """Bookkeeping for a single tracked (non-baseline) object."""

    __slots__ = ("first_seen_index", "first_seen_time", "type_id", "type_name", "repr")

    def __init__(
        self,
        first_seen_index: int,
        first_seen_time: float,
        type_id: int,
        type_name: str,
        repr: str,
    ) -> None:
        self.first_seen_index = first_seen_index
        self.first_seen_time = first_seen_time
        self.type_id = type_id
        self.type_name = type_name
        self.repr = repr


class GCMonitor:
    def __init__(
        self,
        interval: float = 10.0,
        output_dir: str = "gc_snapshots",
        collect: bool = True,
        max_samples_per_type: int = 5,
        repr_max_len: int = 80,
        exclude_types: Iterable[str] | None = None,
        reference_chains: bool = True,
        max_chain_depth: int = 8,
    ) -> None:
        self.interval = interval
        self.output_dir = output_dir
        self.collect = collect
        self.max_samples_per_type = max_samples_per_type
        self.repr_max_len = repr_max_len
        self.reference_chains = reference_chains
        self.max_chain_depth = max_chain_depth
        self.exclude_types: frozenset[str] = (
            DEFAULT_EXCLUDED_TYPES if exclude_types is None else frozenset(exclude_types)
        )

        self._baseline_ids: frozenset[int] = frozenset()
        self._tracked: dict[int, _Track] = {}
        self._type_registry: dict[int, str] = {}
        self._snapshot_index: int = 0

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return

        if self.collect:
            gc.collect()
        self._baseline_ids = frozenset(id(o) for o in gc.get_objects())

        os.makedirs(self.output_dir, exist_ok=True)

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="gcmonitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join()
            self._thread = None

    @staticmethod
    def _type_name(t: type) -> str:
        try:
            return f"{t.__module__}.{t.__qualname__}"
        except Exception:
            return "<unknown>"

    def _is_excluded(self, name: str) -> bool:
        if not self.exclude_types:
            return False
        short = name.rsplit(".", 1)[-1]
        return name in self.exclude_types or short in self.exclude_types

    def _register_type(self, t: type) -> int:
        tid = id(t)
        if tid not in self._type_registry:
            self._type_registry[tid] = self._type_name(t)
        return tid

    def _run(self) -> None:
        # Take an immediate first snapshot, then wait between subsequent ones.
        while True:
            self._take_snapshot()
            if self._stop_event.wait(self.interval):
                break

    def _internal_ids(self, extra: set[int]) -> set[int]:
        """Ids of the monitor's own objects, so they are never seen as leaks."""
        ids = set(extra)
        roots: list[Any] = [
            self,
            self._tracked,
            self._type_registry,
            self._baseline_ids,
            self._stop_event,
        ]
        if self._thread is not None:
            roots.append(self._thread)
        for r in roots:
            ids.add(id(r))
            d = getattr(r, "__dict__", None)
            if isinstance(d, dict):
                ids.add(id(d))
                for v in d.values():
                    ids.add(id(v))
        return ids

    def _build_generation_lookup(self) -> dict[int, int]:
        obj_generation: dict[int, int] = {}
        for gen in (0, 1, 2):
            for o in gc.get_objects(generation=gen):
                obj_generation[id(o)] = gen
        return obj_generation

    def _take_snapshot(self) -> None:
        t_start = time.perf_counter()

        t_collect = time.perf_counter()
        if self.collect:
            gc.collect()
        t_collect_ms = (time.perf_counter() - t_collect) * 1000

        t_get_objects = time.perf_counter()
        objects = gc.get_objects()
        t_get_objects_ms = (time.perf_counter() - t_get_objects) * 1000

        obj_generation = self._build_generation_lookup()

        now = time.time()
        index = self._snapshot_index

        # Ids that belong to the monitor's own working structures must never be
        # reported as leaks (the background thread, its Event, and their
        # attributes are all created after the baseline).
        self_ids = self._internal_ids({id(objects), id(obj_generation)})

        t_traverse = time.perf_counter()
        alive_ids: set[int] = set()
        n_new = 0
        for obj in objects:
            oid = id(obj)
            if oid in self._baseline_ids or oid in self_ids:
                continue

            track = self._tracked.get(oid)
            if track is None:
                t = type(obj)
                name = self._type_name(t)
                if self._is_excluded(name):
                    continue
                tid = self._register_type(t)
                self._tracked[oid] = _Track(
                    first_seen_index=index,
                    first_seen_time=now,
                    type_id=tid,
                    type_name=name,
                    repr=_safe_repr(obj, self.repr_max_len),
                )
                n_new += 1
            alive_ids.add(oid)

        # Drop tracked objects that are no longer alive.
        dead_ids = [oid for oid in self._tracked if oid not in alive_ids]
        for oid in dead_ids:
            del self._tracked[oid]
        n_died = len(dead_ids)

        # For reference-chain reconstruction we need the live objects by id.
        # This map (and `objects`) reference everything, so they must be
        # excluded from any referrer walk to avoid self-contamination.
        id_to_obj: dict[int, Any] = {}
        if self.reference_chains:
            id_to_obj = {id(o): o for o in objects}
        ignore_ids = set(self_ids)
        ignore_ids.add(id(id_to_obj))

        candidates = self._build_candidates(
            alive_ids, obj_generation, index, now, id_to_obj, ignore_ids
        )
        t_traverse_ms = (time.perf_counter() - t_traverse) * 1000

        t_total_ms = (time.perf_counter() - t_start) * 1000

        payload: dict[str, Any] = {
            "meta": {
                "snapshot_index": index,
                "timestamp": now,
                "interval": self.interval,
                "baseline_size": len(self._baseline_ids),
                "n_tracked": len(self._tracked),
                "n_new_this_snapshot": n_new,
                "n_died_since_last": n_died,
                "timings_ms": {
                    "gc_collect": round(t_collect_ms, 3),
                    "get_objects": round(t_get_objects_ms, 3),
                    "traverse": round(t_traverse_ms, 3),
                    "total": round(t_total_ms, 3),
                },
            },
            "candidates_by_type": candidates,
        }

        self._write(payload, index, now)
        self._snapshot_index += 1

    def _build_candidates(
        self,
        alive_ids: set[int],
        obj_generation: dict[int, int],
        index: int,
        now: float,
        id_to_obj: dict[int, Any],
        ignore_ids: set[int],
    ) -> list[dict[str, Any]]:
        # Group currently-alive tracked objects by type.
        by_type: dict[int, list[int]] = defaultdict(list)
        for oid in alive_ids:
            track = self._tracked.get(oid)
            if track is not None:
                by_type[track.type_id].append(oid)

        candidates: list[dict[str, Any]] = []
        for tid, oids in by_type.items():
            # Sort objects within the type oldest-first.
            oids.sort(key=lambda o: self._tracked[o].first_seen_index)

            generations: dict[str, int] = defaultdict(int)
            for oid in oids:
                gen = obj_generation.get(oid)
                generations[str(gen)] += 1

            oldest = self._tracked[oids[0]]
            max_age_snapshots = index - oldest.first_seen_index
            max_age_seconds = round(now - oldest.first_seen_time, 3)

            samples: list[dict[str, Any]] = []
            for oid in oids[: self.max_samples_per_type]:
                track = self._tracked[oid]
                sample: dict[str, Any] = {
                    "id": str(oid),
                    "type": track.type_name,
                    "repr": track.repr,
                    "age_snapshots": index - track.first_seen_index,
                    "age_seconds": round(now - track.first_seen_time, 3),
                    "generation": obj_generation.get(oid),
                }
                if self.reference_chains:
                    target = id_to_obj.get(oid)
                    if target is not None:
                        sample["reference_chain"] = self._reference_chain(
                            target, ignore_ids
                        )
                samples.append(sample)

            candidates.append(
                {
                    "type_name": self._type_registry.get(tid, "<unknown>"),
                    "count": len(oids),
                    "max_age_snapshots": max_age_snapshots,
                    "max_age_seconds": max_age_seconds,
                    "generations": dict(generations),
                    "samples": samples,
                }
            )

        candidates.sort(
            key=lambda c: (c["max_age_snapshots"], c["count"]),
            reverse=True,
        )
        return candidates

    @staticmethod
    def _referrer_score(r: Any) -> int:
        # Bias the walk toward objects that lead to a GC root: modules first,
        # then namespace-like dicts, then everything else.
        if isinstance(r, ModuleType):
            return 3
        if isinstance(r, dict):
            return 2
        return 1

    @staticmethod
    def _edge_descr(holder: Any, held: Any) -> str | None:
        """Describe how ``holder`` references ``held`` (best-effort)."""
        try:
            if isinstance(holder, dict):
                for k, v in holder.items():
                    if v is held:
                        return f"dict key {k!r}"
            if isinstance(holder, (list, tuple)):
                for i, v in enumerate(holder):
                    if v is held:
                        return f"index {i}"
            if isinstance(holder, (set, frozenset)):
                return "set member"
            d = getattr(holder, "__dict__", None)
            if isinstance(d, dict):
                for k, v in d.items():
                    if v is held:
                        return f"attribute {k!r}"
        except Exception:
            pass
        return None

    def _reference_chain(
        self, target: Any, ignore_ids: set[int]
    ) -> list[dict[str, Any]]:
        """Walk referrers from ``target`` toward a root, explaining retention.

        Each chain entry describes a holder that keeps the previous object
        alive, plus how it references it (``via``). The walk stops at a module
        (a GC root), on a cycle, when no eligible referrer remains, or at
        ``max_chain_depth``.
        """
        chain: list[dict[str, Any]] = []
        visited: set[int] = {id(target)}
        current = target

        for _ in range(self.max_chain_depth):
            referrers = gc.get_referrers(current)
            best: Any = None
            best_score = -1
            for r in referrers:
                rid = id(r)
                if rid in ignore_ids or rid in visited:
                    continue
                # Frames are transient roots and include our own call stack;
                # following them is noise for data-structure leaks.
                if isinstance(r, FrameType):
                    continue
                score = self._referrer_score(r)
                if score > best_score:
                    best = r
                    best_score = score
            del referrers
            if best is None:
                break

            chain.append(
                {
                    "id": str(id(best)),
                    "type_name": self._type_name(type(best)),
                    "repr": _safe_repr(best, self.repr_max_len),
                    "via": self._edge_descr(best, current),
                }
            )
            visited.add(id(best))
            if isinstance(best, ModuleType):
                break
            current = best

        return chain

    def _write(self, payload: dict[str, Any], index: int, now: float) -> None:
        filename = f"snapshot_{index:04d}_{int(now)}.json"
        path = os.path.join(self.output_dir, filename)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)


def start(**kwargs: Any) -> GCMonitor:
    monitor = GCMonitor(**kwargs)
    monitor.start()
    return monitor
