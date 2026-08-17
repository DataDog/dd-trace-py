"""Partition collected pytest items and verify runtime-shard inventory artifacts."""

from __future__ import annotations

from collections import Counter
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import typing as t

from .manifest import load_manifest
from .planner import AllocationError
from .planner import parse_execution_unit


SELECTION_ALGORITHM = "sha256-round-robin-v1"


def _digest(values: t.Iterable[str]) -> str:
    encoded = json.dumps(sorted(values), separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def partition_nodeids(nodeids: t.Iterable[str], shard_total: int) -> dict[str, int]:
    """Assign runtime-discovered node IDs evenly and deterministically."""
    values = list(nodeids)
    if not values or len(values) != len(set(values)):
        raise AllocationError("runtime test inventory must be non-empty and unique")
    if shard_total <= 0 or shard_total > len(values):
        raise AllocationError("runtime test shard total must be within the collected item count")

    # Hash ordering spreads parametrized families and slow files that are adjacent
    # in pytest collection order. Round-robin then guarantees count skew <= 1.
    ordered = sorted(values, key=lambda value: (hashlib.sha256(value.encode()).digest(), value))
    return {nodeid: index % shard_total + 1 for index, nodeid in enumerate(ordered)}


def build_runtime_inventory(
    *,
    suite: str,
    riot_hash: str,
    shard_index: int,
    shard_total: int,
    collected_nodeids: t.Iterable[str],
) -> dict[str, t.Any]:
    """Create compact exact-set evidence for one runtime pytest slice."""
    collected = sorted(collected_nodeids)
    assignments = partition_nodeids(collected, shard_total)
    selected = sorted(nodeid for nodeid in collected if assignments[nodeid] == shard_index)
    if not selected:
        raise AllocationError(f"runtime test shard {shard_index}/{shard_total} is empty")
    return {
        "schema_version": 1,
        "kind": "ci-runtime-test-shard-inventory",
        "selection_algorithm": SELECTION_ALGORITHM,
        "suite": suite,
        "riot_hash": riot_hash,
        "shard_index": shard_index,
        "shard_total": shard_total,
        "collection_count": len(collected),
        "collection_sha256": _digest(collected),
        "selected_count": len(selected),
        "selected_sha256": _digest(selected),
        "selected_nodeids": selected,
        # One full inventory per hash is enough to prove the selected-slice
        # union without repeating thousands of node IDs in every artifact.
        "collected_nodeids": collected if shard_index == 1 else None,
    }


def write_runtime_inventory(path: Path, inventory: t.Mapping[str, t.Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _load_inventory(path: Path) -> dict[str, t.Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AllocationError(f"cannot read runtime test inventory {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("kind") != "ci-runtime-test-shard-inventory":
        raise AllocationError(f"runtime test inventory is malformed: {path}")
    return value


def verify_runtime_inventories(
    paths: t.Iterable[Path], plan_path: Path, strategy: str = "balanced"
) -> dict[str, t.Any]:
    """Prove every planned sub-hash slice collected an exact disjoint test set."""
    if strategy != "balanced":
        raise AllocationError("runtime test inventory verification requires the balanced strategy")
    plan = load_manifest(plan_path)
    expected: set[tuple[str, str, int, int]] = set()
    for suite_plan in plan["suites"]:
        suite = suite_plan["suite"]
        for assignment in suite_plan[strategy]["assignments"]:
            for unit in assignment:
                riot_hash, shard_index, shard_total = parse_execution_unit(unit)
                if shard_total > 1:
                    expected.add((suite, riot_hash, shard_index, shard_total))

    inventories = [_load_inventory(path) for path in paths]
    observed: dict[tuple[str, str, int, int], dict[str, t.Any]] = {}
    for inventory in inventories:
        key = (
            str(inventory.get("suite", "")),
            str(inventory.get("riot_hash", "")),
            int(inventory.get("shard_index", 0)),
            int(inventory.get("shard_total", 0)),
        )
        if key in observed:
            raise AllocationError(f"duplicate runtime test inventory: {key}")
        observed[key] = inventory
    if set(observed) != expected:
        missing = sorted(expected - set(observed))[:10]
        unexpected = sorted(set(observed) - expected)[:10]
        raise AllocationError(
            f"runtime test inventories differ from the plan: missing={missing}, unexpected={unexpected}"
        )

    grouped: dict[tuple[str, str, int], list[dict[str, t.Any]]] = defaultdict(list)
    for (suite, riot_hash, _index, total), inventory in observed.items():
        grouped[(suite, riot_hash, total)].append(inventory)

    selected_total = 0
    collection_total = 0
    for (suite, riot_hash, total), group in grouped.items():
        if {int(item["shard_index"]) for item in group} != set(range(1, total + 1)):
            raise AllocationError(f"runtime test shard indices are incomplete for {suite} {riot_hash}")
        collection_counts = {int(item["collection_count"]) for item in group}
        collection_digests = {str(item["collection_sha256"]) for item in group}
        algorithms = {str(item["selection_algorithm"]) for item in group}
        if len(collection_counts) != 1 or len(collection_digests) != 1 or algorithms != {SELECTION_ALGORITHM}:
            raise AllocationError(f"runtime test collection differs across slices for {suite} {riot_hash}")
        if any(int(item["selected_count"]) <= 0 for item in group):
            raise AllocationError(f"runtime test shard is empty for {suite} {riot_hash}")

        full_values = [item.get("collected_nodeids") for item in group if item.get("collected_nodeids") is not None]
        if len(full_values) != 1 or not isinstance(full_values[0], list):
            raise AllocationError(f"runtime test collection inventory is missing for {suite} {riot_hash}")
        collected = [str(value) for value in full_values[0]]
        selected = [str(value) for item in group for value in item.get("selected_nodeids", [])]
        if Counter(selected) != Counter(collected):
            raise AllocationError(f"runtime test shard union differs from collection for {suite} {riot_hash}")
        if _digest(collected) != next(iter(collection_digests)):
            raise AllocationError(f"runtime test collection digest differs for {suite} {riot_hash}")
        for item in group:
            values = [str(value) for value in item.get("selected_nodeids", [])]
            if len(values) != int(item["selected_count"]) or _digest(values) != item["selected_sha256"]:
                raise AllocationError(f"runtime test shard digest differs for {suite} {riot_hash}")
        selected_total += len(selected)
        collection_total += len(collected)

    return {
        "schema_version": 1,
        "kind": "ci-runtime-test-shard-parity",
        "split_hash_count": len(grouped),
        "runtime_slice_count": len(observed),
        "test_identity_count": selected_total,
        "collection_identity_count": collection_total,
        "exact_union": True,
        "no_overlap": True,
        "no_empty_slices": True,
    }
