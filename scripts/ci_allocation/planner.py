"""Build and verify deterministic shard assignments."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import typing as t


class AllocationError(RuntimeError):
    pass


def _normalize_hashes(riot_hashes: t.Iterable[str]) -> list[str]:
    hashes = sorted(riot_hashes)
    if not hashes:
        raise AllocationError("an allocation requires at least one Riot hash")
    if len(hashes) != len(set(hashes)):
        raise AllocationError("an allocation cannot contain duplicate Riot hashes")
    return hashes


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
) -> dict[str, t.Any]:
    """Build legacy and balanced plans with exact-set parity evidence."""
    hashes = _normalize_hashes(riot_hashes)
    legacy = legacy_round_robin(hashes, shard_count)
    candidate_shard_count = balanced_shard_count if balanced_shard_count is not None else shard_count
    balanced = weighted_lpt(hashes, candidate_shard_count, estimates, fallback_seconds)
    verify_assignments(hashes, legacy)
    verify_assignments(hashes, balanced)
    if not math.isfinite(overhead_seconds) or overhead_seconds < 0:
        raise AllocationError("overhead_seconds must be finite and non-negative")

    # AIDEV-NOTE: Runtime history may change placement, but this exact-set proof is
    # the correctness authority. Never relax it to make a recommendation pass.
    return {
        "suite": suite,
        "execution_metadata_sha256": metadata_digest(execution_metadata),
        "riot_hashes": hashes,
        "legacy": {
            "algorithm": "sorted-round-robin-v1",
            "shard_count": shard_count,
            "assignments": legacy,
            "predicted_makespan_seconds": overhead_seconds + predicted_makespan(legacy, estimates, fallback_seconds),
        },
        "balanced": {
            "algorithm": "weighted-lpt-v1",
            "shard_count": candidate_shard_count,
            "assignments": balanced,
            "predicted_makespan_seconds": overhead_seconds + predicted_makespan(balanced, estimates, fallback_seconds),
        },
        "parity": {
            "exact_union": True,
            "no_overlap": True,
            "no_empty_shards": True,
            "execution_metadata_equal": True,
        },
    }


def selected_shard(plan: dict[str, t.Any], strategy: str, node_index: int) -> list[str]:
    if strategy not in {"legacy", "balanced"}:
        raise AllocationError(f"unknown allocation strategy: {strategy}")
    assignments = plan[strategy]["assignments"]
    if node_index <= 0 or node_index > len(assignments):
        raise AllocationError("node_index is outside the plan")
    return list(assignments[node_index - 1])
