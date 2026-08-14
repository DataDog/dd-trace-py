"""Resolve semantic suites into their Riot execution units."""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
import re
import typing as t

from .planner import predicted_makespan
from .planner import weighted_lpt


@dataclass(frozen=True)
class SuiteVenvInfo:
    """The Riot hashes and Python versions selected by one semantic suite."""

    hashes: tuple[str, ...]
    python_versions: frozenset[str]

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

    for instance in riotfile.venv.instances():  # type: ignore[attr-defined]
        if not instance.name:
            continue
        hint = instance.py._hint
        for suite, regex in compiled.items():
            if instance.matches_pattern(regex):
                venv_hashes[suite].add(instance.short_hash)
                if re.match(r"^3\.\d+$", hint):
                    python_versions[suite].add(hint)

    return {
        suite: SuiteVenvInfo(
            hashes=tuple(sorted(hashes)),
            python_versions=frozenset(python_versions[suite]),
        )
        for suite, hashes in venv_hashes.items()
        if hashes
    }


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
) -> dict[str, int]:
    """Size suites from modeled work within an optional global job budget."""
    if target_shard_seconds <= 0:
        raise ValueError("target_shard_seconds must be positive")
    if maximum_parallelism_per_suite <= 0:
        raise ValueError("maximum_parallelism_per_suite must be positive")

    result = {}
    suite_overheads = suite_overheads or {}
    for suite in selected_suites:
        info = suite_venv_info.get(suite)
        if info is None:
            continue
        fallback = float(suite_fallbacks.get(suite, global_fallback))
        overhead = float(suite_overheads.get(suite, global_overhead))
        if overhead < 0:
            raise ValueError("suite overhead cannot be negative")
        modeled_work = sum(float(estimates.get(riot_hash, fallback)) for riot_hash in info.hashes)
        available_seconds = max(1.0, target_shard_seconds - overhead)
        result[suite] = min(
            info.venv_count,
            maximum_parallelism_per_suite,
            max(1, math.ceil(modeled_work / available_seconds)),
        )

    if maximum_total_jobs is None or sum(result.values()) <= maximum_total_jobs:
        return result
    if maximum_total_jobs < len(result):
        raise ValueError("maximum_total_jobs cannot be smaller than the selected suite count")

    def removal_penalty(suite: str, shard_count: int) -> float:
        info = suite_venv_info[suite]
        fallback = float(suite_fallbacks.get(suite, global_fallback))
        current = weighted_lpt(info.hashes, shard_count, estimates, fallback)
        reduced = weighted_lpt(info.hashes, shard_count - 1, estimates, fallback)
        return predicted_makespan(reduced, estimates, fallback) - predicted_makespan(current, estimates, fallback)

    # AIDEV-NOTE: The legacy total is a cost ceiling, not a per-suite ceiling.
    # Remove the shard with the smallest modeled critical-path penalty so capacity
    # can move from over-sharded suites to measured long poles deterministically.
    candidates = [(removal_penalty(suite, count), suite, count) for suite, count in result.items() if count > 1]
    heapq.heapify(candidates)
    while sum(result.values()) > maximum_total_jobs:
        if not candidates:
            raise ValueError("maximum_total_jobs cannot be satisfied")
        _penalty, suite, expected_count = heapq.heappop(candidates)
        if result[suite] != expected_count:
            continue
        result[suite] -= 1
        if result[suite] > 1:
            heapq.heappush(
                candidates,
                (removal_penalty(suite, result[suite]), suite, result[suite]),
            )
    return result
