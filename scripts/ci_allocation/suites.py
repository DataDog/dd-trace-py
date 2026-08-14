"""Resolve semantic suites into their Riot execution units."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
import hashlib
import heapq
import math
import re
import typing as t

from .planner import expand_runtime_units
from .planner import parse_execution_unit
from .planner import predicted_makespan
from .planner import weighted_runtime_lpt


@dataclass(frozen=True)
class SuiteVenvInfo:
    """The Riot hashes and Python versions selected by one semantic suite."""

    hashes: tuple[str, ...]
    python_versions: frozenset[str]
    commands: t.Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    python_version_by_hash: t.Mapping[str, str] = field(default_factory=dict)

    @property
    def venv_count(self) -> int:
        return len(self.hashes)


def collect_all_suite_venv_info(suite_patterns: dict[str, str]) -> dict[str, SuiteVenvInfo]:
    """Collect Riot hashes and Python versions for many suites in one pass."""
    import riotfile

    compiled: dict[str, re.Pattern[str]] = {}
    for suite, pattern in suite_patterns.items():
        try:
            compiled[suite] = re.compile(pattern)
        except re.error:
            continue

    venv_hashes: dict[str, set[str]] = {suite: set() for suite in compiled}
    python_versions: dict[str, set[str]] = {suite: set() for suite in compiled}
    commands: dict[str, dict[str, set[str]]] = {suite: defaultdict(set) for suite in compiled}
    versions_by_hash: dict[str, dict[str, str]] = {suite: {} for suite in compiled}

    for instance in riotfile.venv.instances():  # type: ignore[attr-defined]
        if not instance.name:
            continue
        hint = instance.py._hint
        for suite, regex in compiled.items():
            if instance.matches_pattern(regex):
                venv_hashes[suite].add(instance.short_hash)
                commands[suite][instance.short_hash].add(str(instance.command or ""))
                if re.match(r"^3\.\d+$", hint):
                    python_versions[suite].add(hint)
                    versions_by_hash[suite][instance.short_hash] = hint

    return {
        suite: SuiteVenvInfo(
            hashes=tuple(sorted(hashes)),
            python_versions=frozenset(python_versions[suite]),
            commands={riot_hash: tuple(sorted(values)) for riot_hash, values in sorted(commands[suite].items())},
            python_version_by_hash=dict(sorted(versions_by_hash[suite].items())),
        )
        for suite, hashes in venv_hashes.items()
        if hashes
    }


def runtime_test_item_counts(info: SuiteVenvInfo, runtime_model: t.Mapping[str, t.Any]) -> dict[str, int]:
    """Resolve command-level Test Visibility evidence to this suite's Riot hashes."""
    test_sharding = runtime_model.get("test_sharding", {})
    command_evidence = test_sharding.get("command_fingerprints", {}) if isinstance(test_sharding, dict) else {}
    if not isinstance(command_evidence, dict):
        return {}

    result = {}
    for riot_hash, commands in info.commands.items():
        commands = (commands,) if isinstance(commands, str) else commands
        counts = []
        for command in commands:
            fingerprint = hashlib.sha256(command.encode()).hexdigest()
            evidence = command_evidence.get(fingerprint)
            item_count = evidence.get("minimum_items") if isinstance(evidence, dict) else None
            if not isinstance(item_count, int) or isinstance(item_count, bool) or item_count <= 0:
                break
            counts.append(item_count)
        else:
            if counts:
                result[riot_hash] = min(counts)
    return result


def runtime_setup_seconds(runtime_model: t.Mapping[str, t.Any], suite: str) -> float:
    """Return measured setup/activation time for one new runtime execution unit."""
    overheads = runtime_model.get("overheads", {})
    if not isinstance(overheads, dict):
        return 0.0
    suite_values = overheads.get("unit_suite_seconds", {})
    if isinstance(suite_values, dict) and suite in suite_values:
        return float(suite_values[suite])
    stage_values = overheads.get("unit_stage_seconds", {})
    stage = suite.split("::", 1)[0] if "::" in suite else "core"
    if isinstance(stage_values, dict) and stage in stage_values:
        return float(stage_values[stage])
    return float(overheads.get("unit_global_seconds", 0.0))


def calculate_parallelism_from_venvs(venv_count: int, venvs_per_job: int, max_parallelism: int = 25) -> int:
    """Calculate the baseline shard count from a suite's Riot environment count."""
    if venv_count <= 0:
        raise ValueError("venv_count must be positive")
    if venvs_per_job <= 0:
        raise ValueError("venvs_per_job must be positive")
    if max_parallelism <= 0:
        raise ValueError("max_parallelism must be positive")
    return min(math.ceil(venv_count / venvs_per_job), max_parallelism)


def scale_suites(
    suite_venv_info: dict[str, SuiteVenvInfo],
    final_jobs: dict[str, int],
    scalable_suites: list[str],
    venvs_per_job_map: dict[str, int],
    target: int,
) -> dict[str, int]:
    """Scale selected suites toward a total job target without splitting Riot hashes."""
    if target <= 0:
        raise ValueError("target must be positive")

    final_jobs = dict(final_jobs)
    current_vpj = dict(venvs_per_job_map)

    while sum(final_jobs.values()) < target:
        best_gain = 0
        best_suite: t.Optional[str] = None

        for suite in scalable_suites:
            venv_count = suite_venv_info[suite].venv_count
            current = final_jobs[suite]
            if current >= venv_count:
                continue

            if suite in current_vpj:
                vpj = current_vpj[suite]
                if vpj <= 1:
                    continue
                new_parallelism = math.ceil(venv_count / (vpj - 1))
            else:
                new_parallelism = current + 1

            gain = new_parallelism - current
            if gain > best_gain:
                best_gain = gain
                best_suite = suite

        if best_suite is None or best_gain == 0:
            break

        venv_count = suite_venv_info[best_suite].venv_count
        if best_suite in current_vpj:
            current_vpj[best_suite] -= 1
            final_jobs[best_suite] = math.ceil(venv_count / current_vpj[best_suite])
        else:
            final_jobs[best_suite] += 1

    return final_jobs


def compute_parallelism(
    suite_configs: t.Mapping[str, t.Mapping[str, t.Any]],
    selected_suites: t.Iterable[str],
    suite_venv_info: dict[str, SuiteVenvInfo],
    *,
    target_jobs: int,
) -> dict[str, int]:
    """Apply the current baseline and sparse-run scaling policy."""
    selected = [
        suite
        for suite in selected_suites
        if suite in suite_configs and not suite_configs[suite].get("skip", False) and suite in suite_venv_info
    ]
    baseline_jobs: dict[str, int] = {}
    scalable_suites: list[str] = []
    venvs_per_job_map: dict[str, int] = {}
    for suite in selected:
        config = suite_configs[suite]
        static_parallelism = config.get("parallelism")
        venvs_per_job = config.get("venvs_per_job")
        if static_parallelism is not None:
            baseline_jobs[suite] = int(static_parallelism)
            scalable_suites.append(suite)
        elif venvs_per_job is not None:
            baseline_jobs[suite] = calculate_parallelism_from_venvs(
                suite_venv_info[suite].venv_count, int(venvs_per_job)
            )
            scalable_suites.append(suite)
            venvs_per_job_map[suite] = int(venvs_per_job)
        else:
            baseline_jobs[suite] = 1

    if sum(baseline_jobs.values()) < target_jobs and scalable_suites:
        return scale_suites(
            suite_venv_info,
            baseline_jobs,
            scalable_suites,
            venvs_per_job_map,
            target_jobs,
        )
    return baseline_jobs


def compute_runtime_parallelism(
    suite_venv_info: t.Mapping[str, SuiteVenvInfo],
    selected_suites: t.Iterable[str],
    estimates: t.Mapping[str, float],
    suite_fallbacks: t.Mapping[str, float],
    global_fallback: float,
    *,
    target_shard_seconds: float,
    maximum_parallelism_per_suite: int,
    maximum_total_jobs: t.Optional[int] = None,
    suite_overheads: t.Optional[t.Mapping[str, float]] = None,
    global_overhead: float = 0.0,
    test_item_counts_by_suite: t.Optional[t.Mapping[str, t.Mapping[str, int]]] = None,
    estimates_by_suite: t.Optional[t.Mapping[str, t.Mapping[str, float]]] = None,
    maximum_slices_per_hash: int = 1,
) -> dict[str, int]:
    """Size suites from modeled work within an optional global job budget."""
    if target_shard_seconds <= 0:
        raise ValueError("target_shard_seconds must be positive")
    if maximum_parallelism_per_suite <= 0:
        raise ValueError("maximum_parallelism_per_suite must be positive")

    result = {}
    suite_overheads = suite_overheads or {}
    test_item_counts_by_suite = test_item_counts_by_suite or {}
    estimates_by_suite = estimates_by_suite or {}
    runtime_units: dict[str, tuple[list[str], dict[str, float]]] = {}
    minimum_jobs: dict[str, int] = {}
    for suite in selected_suites:
        info = suite_venv_info.get(suite)
        if info is None:
            continue
        fallback = float(suite_fallbacks.get(suite, global_fallback))
        suite_estimates = estimates_by_suite.get(suite, estimates)
        overhead = float(suite_overheads.get(suite, global_overhead))
        if overhead < 0:
            raise ValueError("suite overhead cannot be negative")
        units, weights = expand_runtime_units(
            info.hashes,
            suite_estimates,
            fallback,
            target_shard_seconds=target_shard_seconds,
            setup_seconds=overhead,
            test_item_counts=test_item_counts_by_suite.get(suite, {}),
            maximum_slices_per_hash=maximum_slices_per_hash,
        )
        runtime_units[suite] = (units, weights)
        minimum_jobs[suite] = max(parse_execution_unit(unit)[2] for unit in units)
        modeled_work = sum(weights.values())
        result[suite] = min(
            len(units),
            maximum_parallelism_per_suite,
            max(minimum_jobs[suite], math.ceil(modeled_work / target_shard_seconds)),
        )
        if result[suite] < minimum_jobs[suite]:
            raise ValueError(f"maximum_parallelism_per_suite cannot place runtime slices for {suite}")

    if maximum_total_jobs is None or sum(result.values()) <= maximum_total_jobs:
        return result
    if maximum_total_jobs < sum(minimum_jobs.values()):
        raise ValueError("maximum_total_jobs cannot place every runtime slice")

    def removal_penalty(suite: str, shard_count: int) -> float:
        units, weights = runtime_units[suite]
        current = weighted_runtime_lpt(units, shard_count, weights)
        reduced = weighted_runtime_lpt(units, shard_count - 1, weights)
        return predicted_makespan(reduced, weights, global_fallback) - predicted_makespan(
            current, weights, global_fallback
        )

    # AIDEV-NOTE: The legacy total is a cost ceiling, not a per-suite ceiling.
    # Remove the shard with the smallest modeled critical-path penalty so capacity
    # can move from over-sharded suites to measured long poles deterministically.
    candidates = [
        (removal_penalty(suite, count), suite, count) for suite, count in result.items() if count > minimum_jobs[suite]
    ]
    heapq.heapify(candidates)
    while sum(result.values()) > maximum_total_jobs:
        if not candidates:
            raise ValueError("maximum_total_jobs cannot be satisfied")
        _penalty, suite, expected_count = heapq.heappop(candidates)
        if result[suite] != expected_count:
            continue
        result[suite] -= 1
        if result[suite] > minimum_jobs[suite]:
            heapq.heappush(
                candidates,
                (removal_penalty(suite, result[suite]), suite, result[suite]),
            )
    return result
