"""Build and verify deterministic shard assignments."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
import typing as t


class AllocationError(RuntimeError):
    pass


EXECUTION_UNIT = re.compile(r"^(?P<riot_hash>[0-9a-f]+)(?:@(?P<index>[1-9][0-9]*)/(?P<total>[1-9][0-9]*))?$")


def _normalize_hashes(riot_hashes: t.Iterable[str]) -> list[str]:
    hashes = sorted(riot_hashes)
    if not hashes:
        raise AllocationError("an allocation requires at least one Riot hash")
    if len(hashes) != len(set(hashes)):
        raise AllocationError("an allocation cannot contain duplicate Riot hashes")
    return hashes


def execution_unit_id(riot_hash: str, shard_index: int = 1, shard_total: int = 1) -> str:
    """Encode one whole or runtime-sliced Riot environment."""
    if not re.fullmatch(r"[0-9a-f]+", riot_hash):
        raise AllocationError(f"invalid Riot hash: {riot_hash}")
    if shard_total <= 0 or shard_index <= 0 or shard_index > shard_total:
        raise AllocationError("runtime shard index must be within its positive shard total")
    return riot_hash if shard_total == 1 else f"{riot_hash}@{shard_index}/{shard_total}"


def parse_execution_unit(value: str) -> tuple[str, int, int]:
    """Decode an execution unit into Riot hash, runtime index, and runtime total."""
    match = EXECUTION_UNIT.fullmatch(value)
    if match is None:
        raise AllocationError(f"invalid Riot execution unit: {value}")
    shard_index = int(match.group("index") or 1)
    shard_total = int(match.group("total") or 1)
    if shard_index > shard_total:
        raise AllocationError(f"runtime shard index exceeds its total: {value}")
    return match.group("riot_hash"), shard_index, shard_total


def expand_runtime_units(
    riot_hashes: t.Iterable[str],
    estimates: t.Mapping[str, float],
    fallback_seconds: float,
    *,
    target_shard_seconds: float,
    setup_seconds: float,
    test_item_counts: t.Optional[t.Mapping[str, int]] = None,
    maximum_slices_per_hash: int = 1,
) -> tuple[list[str], dict[str, float]]:
    """Expand measured pytest environments into independently runnable test slices."""
    hashes = _normalize_hashes(riot_hashes)
    if not math.isfinite(target_shard_seconds) or target_shard_seconds <= 0:
        raise AllocationError("target_shard_seconds must be finite and positive")
    if not math.isfinite(setup_seconds) or setup_seconds < 0:
        raise AllocationError("setup_seconds must be finite and non-negative")
    if maximum_slices_per_hash <= 0:
        raise AllocationError("maximum_slices_per_hash must be positive")
    if target_shard_seconds <= setup_seconds and maximum_slices_per_hash > 1:
        raise AllocationError("target_shard_seconds must exceed setup_seconds for runtime slicing")

    test_item_counts = test_item_counts or {}
    units: list[str] = []
    weights: dict[str, float] = {}
    for riot_hash in hashes:
        estimate = float(estimates.get(riot_hash, fallback_seconds))
        if not math.isfinite(estimate) or estimate <= 0:
            raise AllocationError(f"invalid duration estimate for Riot hash {riot_hash}")

        # Only hashes backed by actual test-item observations may be split. A
        # fallback duration can describe a one-test or non-pytest environment.
        item_count = int(test_item_counts.get(riot_hash, 0))
        slice_count = 1
        if riot_hash in estimates and item_count > 1 and estimate > target_shard_seconds:
            test_seconds = max(1.0, estimate - setup_seconds)
            available_seconds = target_shard_seconds - setup_seconds
            slice_count = min(
                item_count,
                maximum_slices_per_hash,
                max(2, math.ceil(test_seconds / available_seconds)),
            )

        sliced_test_seconds = max(1.0, estimate - setup_seconds) / slice_count
        for shard_index in range(1, slice_count + 1):
            unit = execution_unit_id(riot_hash, shard_index, slice_count)
            units.append(unit)
            weights[unit] = estimate if slice_count == 1 else setup_seconds + sliced_test_seconds
    return units, weights


def verify_runtime_assignments(expected_hashes: t.Iterable[str], assignments: list[list[str]]) -> None:
    """Prove runtime slices cover each semantic Riot hash exactly once."""
    expected = _normalize_hashes(expected_hashes)
    if not assignments:
        raise AllocationError("an allocation must contain at least one shard")
    if any(not shard for shard in assignments):
        raise AllocationError("an allocation cannot contain an empty shard")

    flattened = [unit for shard in assignments for unit in shard]
    if len(flattened) != len(set(flattened)):
        raise AllocationError("a Riot execution unit is assigned to more than one shard")

    by_hash: dict[str, list[tuple[int, int]]] = {}
    for unit in flattened:
        riot_hash, shard_index, shard_total = parse_execution_unit(unit)
        by_hash.setdefault(riot_hash, []).append((shard_index, shard_total))
    if sorted(by_hash) != expected:
        missing = sorted(set(expected) - set(by_hash))
        unexpected = sorted(set(by_hash) - set(expected))
        raise AllocationError(f"allocation differs from the semantic suite: missing={missing}, unexpected={unexpected}")
    for riot_hash, slices in by_hash.items():
        totals = {total for _index, total in slices}
        if len(totals) != 1:
            raise AllocationError(f"runtime shard totals differ for Riot hash {riot_hash}")
        total = next(iter(totals))
        if {index for index, _total in slices} != set(range(1, total + 1)):
            raise AllocationError(f"runtime shards are incomplete for Riot hash {riot_hash}")


def weighted_runtime_lpt(units: t.Iterable[str], shard_count: int, weights: t.Mapping[str, float]) -> list[list[str]]:
    """Pack runtime units by weight while keeping sibling slices in separate jobs."""
    normalized = sorted(units)
    if not normalized or len(normalized) != len(set(normalized)):
        raise AllocationError("runtime execution units must be non-empty and unique")
    if shard_count <= 0 or shard_count > len(normalized):
        raise AllocationError("shard_count must be between one and the execution unit count")

    max_slices = max(parse_execution_unit(unit)[2] for unit in normalized)
    if shard_count < max_slices:
        raise AllocationError("runtime sibling slices require at least one distinct job each")

    shards: list[list[str]] = [[] for _ in range(shard_count)]
    loads = [0.0] * shard_count
    assigned_hashes: list[set[str]] = [set() for _ in range(shard_count)]
    for unit in sorted(normalized, key=lambda item: (-float(weights[item]), item)):
        riot_hash, _index, _total = parse_execution_unit(unit)
        candidates = [index for index in range(shard_count) if riot_hash not in assigned_hashes[index]]
        if not candidates:
            raise AllocationError(f"cannot place sibling runtime slice for Riot hash {riot_hash}")
        shard_index = min(candidates, key=lambda index: (loads[index], index))
        shards[shard_index].append(unit)
        loads[shard_index] += float(weights[unit])
        assigned_hashes[shard_index].add(riot_hash)
    return shards


def legacy_round_robin(riot_hashes: t.Iterable[str], shard_count: int) -> list[list[str]]:
    """Reproduce the current ci-split-input.sh assignment exactly."""
    hashes = _normalize_hashes(riot_hashes)
    if shard_count <= 0 or shard_count > len(hashes):
        raise AllocationError("shard_count must be between one and the Riot hash count")
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    for index, riot_hash in enumerate(hashes):
        shards[index % shard_count].append(riot_hash)
    return shards


def weighted_lpt(
    riot_hashes: t.Iterable[str],
    shard_count: int,
    estimates: t.Mapping[str, float],
    fallback_seconds: float,
) -> list[list[str]]:
    """Assign atomic Riot hashes with deterministic longest-processing-time packing."""
    hashes = _normalize_hashes(riot_hashes)
    if shard_count <= 0 or shard_count > len(hashes):
        raise AllocationError("shard_count must be between one and the Riot hash count")
    if not math.isfinite(fallback_seconds) or fallback_seconds <= 0:
        raise AllocationError("fallback_seconds must be finite and positive")

    weights: dict[str, float] = {}
    for riot_hash in hashes:
        value = float(estimates.get(riot_hash, fallback_seconds))
        if not math.isfinite(value) or value <= 0:
            raise AllocationError(f"invalid duration estimate for Riot hash {riot_hash}")
        weights[riot_hash] = value

    shards: list[list[str]] = [[] for _ in range(shard_count)]
    heap: list[tuple[float, int]] = [(0.0, index) for index in range(shard_count)]
    heapq.heapify(heap)
    for riot_hash in sorted(hashes, key=lambda item: (-weights[item], item)):
        total, shard_index = heapq.heappop(heap)
        shards[shard_index].append(riot_hash)
        heapq.heappush(heap, (total + weights[riot_hash], shard_index))
    return shards


def verify_assignments(expected_hashes: t.Iterable[str], assignments: list[list[str]]) -> None:
    expected = _normalize_hashes(expected_hashes)
    if not assignments:
        raise AllocationError("an allocation must contain at least one shard")
    if any(not shard for shard in assignments):
        raise AllocationError("an allocation cannot contain an empty shard")

    flattened = [riot_hash for shard in assignments for riot_hash in shard]
    if len(flattened) != len(set(flattened)):
        raise AllocationError("a Riot hash is assigned to more than one shard")
    if sorted(flattened) != expected:
        missing = sorted(set(expected) - set(flattened))
        unexpected = sorted(set(flattened) - set(expected))
        raise AllocationError(f"allocation differs from the semantic suite: missing={missing}, unexpected={unexpected}")


def predicted_makespan(
    assignments: list[list[str]], estimates: t.Mapping[str, float], fallback_seconds: float
) -> float:
    return max(sum(float(estimates.get(riot_hash, fallback_seconds)) for riot_hash in shard) for shard in assignments)


def metadata_digest(metadata: t.Mapping[str, t.Any]) -> str:
    encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_suite_plan(
    *,
    suite: str,
    riot_hashes: t.Iterable[str],
    shard_count: int,
    balanced_shard_count: t.Optional[int] = None,
    estimates: t.Mapping[str, float],
    fallback_seconds: float,
    execution_metadata: t.Mapping[str, t.Any],
    overhead_seconds: float = 0.0,
    target_shard_seconds: float = 300.0,
    test_item_counts: t.Optional[t.Mapping[str, int]] = None,
    maximum_slices_per_hash: int = 1,
) -> dict[str, t.Any]:
    """Build legacy and balanced plans with exact-set parity evidence."""
    hashes = _normalize_hashes(riot_hashes)
    legacy = legacy_round_robin(hashes, shard_count)
    candidate_shard_count = balanced_shard_count if balanced_shard_count is not None else shard_count
    runtime_units, runtime_weights = expand_runtime_units(
        hashes,
        estimates,
        fallback_seconds,
        target_shard_seconds=target_shard_seconds,
        setup_seconds=overhead_seconds,
        test_item_counts=test_item_counts,
        maximum_slices_per_hash=maximum_slices_per_hash,
    )
    balanced = weighted_runtime_lpt(runtime_units, candidate_shard_count, runtime_weights)
    verify_assignments(hashes, legacy)
    verify_runtime_assignments(hashes, balanced)
    if not math.isfinite(overhead_seconds) or overhead_seconds < 0:
        raise AllocationError("overhead_seconds must be finite and non-negative")

    # AIDEV-NOTE: Runtime history may change placement or refine a hash into
    # slices, but this exact-set proof is the correctness authority. Never
    # relax it to make a recommendation pass.
    return {
        "suite": suite,
        "execution_metadata_sha256": metadata_digest(execution_metadata),
        "riot_hashes": hashes,
        "legacy": {
            "algorithm": "sorted-round-robin-v1",
            "shard_count": shard_count,
            "assignments": legacy,
            # Fitted Riot estimates already contain one activation/setup cost.
            "predicted_makespan_seconds": predicted_makespan(legacy, estimates, fallback_seconds),
        },
        "balanced": {
            "algorithm": "runtime-sliced-lpt-v2",
            "shard_count": candidate_shard_count,
            "assignments": balanced,
            "execution_unit_count": len(runtime_units),
            "predicted_makespan_seconds": predicted_makespan(balanced, runtime_weights, fallback_seconds),
        },
        "parity": {
            "exact_union": True,
            "no_overlap": True,
            "no_empty_shards": True,
            "execution_metadata_equal": True,
            "complete_runtime_slices": True,
        },
    }


def selected_shard(plan: dict[str, t.Any], strategy: str, node_index: int) -> list[str]:
    if strategy not in {"legacy", "balanced"}:
        raise AllocationError(f"unknown allocation strategy: {strategy}")
    assignments = plan[strategy]["assignments"]
    if node_index <= 0 or node_index > len(assignments):
        raise AllocationError("node_index is outside the plan")
    return list(assignments[node_index - 1])
