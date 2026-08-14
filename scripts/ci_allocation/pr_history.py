"""Replay current semantic suite selection across historical pull-request shapes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from dataclasses import dataclass
import fnmatch
import hashlib
import json
from pathlib import Path
import re
import subprocess
import typing as t

from .history import percentile
from .history import runtime_estimates
from .history import suite_stage
from .planner import AllocationError
from .planner import expand_runtime_units
from .planner import legacy_round_robin
from .planner import predicted_makespan
from .planner import weighted_runtime_lpt
from .suites import SuiteVenvInfo
from .suites import compute_parallelism
from .suites import compute_runtime_parallelism
from .suites import runtime_setup_seconds
from .suites import runtime_test_item_counts


@dataclass(frozen=True)
class PRShape:
    commit_sha: str
    timestamp: str
    subject: str
    changed_files: tuple[str, ...]
    selected_suites: tuple[str, ...]
    cohort: str


def classify_cohort(files: t.Iterable[str]) -> str:
    paths = tuple(files)
    if paths and all(path.startswith(("docs/", "releasenotes/")) or path.endswith((".md", ".rst")) for path in paths):
        return "docs"
    if any(path.startswith((".gitlab/", ".github/", "scripts/")) for path in paths):
        return "ci"
    if any(path.startswith(("ddtrace/appsec/", "tests/appsec/")) for path in paths):
        return "appsec"
    if any(path.startswith(("ddtrace/contrib/", "tests/contrib/")) for path in paths):
        return "integration"
    return "core"


def select_suites(changed_files: t.Iterable[str], suite_patterns: t.Mapping[str, t.Iterable[str]]) -> tuple[str, ...]:
    files = tuple(changed_files)
    selected = []
    for suite in sorted(suite_patterns):
        patterns = tuple(suite_patterns[suite])
        if not patterns or any(fnmatch.filter(files, pattern) for pattern in patterns):
            selected.append(suite)
    return tuple(selected)


def collect_pr_shapes(
    *,
    root: Path,
    suite_patterns: t.Mapping[str, t.Iterable[str]],
    since: str,
    max_count: t.Optional[int] = None,
) -> list[PRShape]:
    """Collect first-parent PR-shaped commits and their changed paths in one Git call."""
    command = [
        "git",
        "log",
        "--first-parent",
        f"--since={since}",
        "--format=%x1e%H%x1f%cI%x1f%s",
        "--name-only",
    ]
    if max_count is not None:
        command.insert(3, f"--max-count={max_count}")
    result = subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
    shapes = []
    for record in result.stdout.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        header, *path_lines = record.splitlines()
        fields = header.split("\x1f", 2)
        if len(fields) != 3:
            raise AllocationError("git history record is malformed")
        commit_sha, timestamp, subject = fields
        if not re.search(r"\(#\d+\)$", subject):
            continue
        changed_files = tuple(sorted({line.strip() for line in path_lines if line.strip()}))
        shapes.append(
            PRShape(
                commit_sha=commit_sha,
                timestamp=timestamp,
                subject=subject,
                changed_files=changed_files,
                selected_suites=select_suites(changed_files, suite_patterns),
                cohort=classify_cohort(changed_files),
            )
        )
    return shapes


def write_pr_shapes(path: Path, shapes: t.Iterable[PRShape]) -> None:
    lines = [json.dumps({"schema_version": 1, **asdict(item)}, sort_keys=True) for item in shapes]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def load_pr_shapes(path: Path) -> list[PRShape]:
    shapes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict) or value.pop("schema_version", None) != 1:
            raise AllocationError("PR history contains an unsupported record")
        value["changed_files"] = tuple(value["changed_files"])
        value["selected_suites"] = tuple(value["selected_suites"])
        shapes.append(PRShape(**value))
    return shapes


def _fingerprint(shapes: t.Iterable[PRShape]) -> str:
    value = [asdict(item) for item in shapes]
    value.sort(key=lambda item: item["commit_sha"])
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def replay_pr_shapes(
    *,
    shapes: list[PRShape],
    suite_configs: t.Mapping[str, t.Mapping[str, t.Any]],
    suite_venv_info: dict[str, SuiteVenvInfo],
    runtime_model: t.Mapping[str, t.Any],
    target_jobs: int,
    target_shard_seconds: float,
    maximum_parallelism_per_suite: int,
    maximum_slices_per_hash: int = 1,
) -> dict[str, t.Any]:
    """Estimate both planners over historical PR path-selection cohorts."""
    if not shapes:
        raise AllocationError("PR replay requires at least one historical shape")
    estimates, global_fallback = runtime_estimates(runtime_model)
    suite_fallbacks = runtime_model["fallbacks"].get("suite_seconds", {})
    estimates_by_suite = {suite: runtime_estimates(runtime_model, suite)[0] for suite in suite_venv_info}
    test_item_counts = {suite: runtime_test_item_counts(info, runtime_model) for suite, info in suite_venv_info.items()}
    setup_seconds = {suite: runtime_setup_seconds(runtime_model, suite) for suite in suite_venv_info}
    results: list[tuple[str, float, float, float, float]] = []
    unmodeled_shapes = 0
    for shape in shapes:
        selected = [suite for suite in shape.selected_suites if suite in suite_venv_info and suite in suite_configs]
        legacy_shard_counts = compute_parallelism(
            suite_configs,
            selected,
            suite_venv_info,
            target_jobs=target_jobs,
        )
        if not legacy_shard_counts:
            unmodeled_shapes += 1
            continue
        balanced_shard_counts = (
            compute_runtime_parallelism(
                suite_venv_info,
                selected,
                estimates,
                suite_fallbacks,
                global_fallback,
                target_shard_seconds=target_shard_seconds,
                maximum_parallelism_per_suite=maximum_parallelism_per_suite,
                maximum_total_jobs=sum(legacy_shard_counts.values()),
                suite_overheads=setup_seconds,
                global_overhead=float(runtime_model["overheads"].get("unit_global_seconds", 0.0)),
                test_item_counts_by_suite=test_item_counts,
                estimates_by_suite=estimates_by_suite,
                maximum_slices_per_hash=maximum_slices_per_hash,
            )
            if estimates
            else legacy_shard_counts
        )
        legacy_stages: dict[str, float] = defaultdict(float)
        balanced_stages: dict[str, float] = defaultdict(float)
        legacy_runner_seconds = 0.0
        balanced_runner_seconds = 0.0
        for suite, legacy_shard_count in legacy_shard_counts.items():
            hashes = suite_venv_info[suite].hashes
            fallback = float(suite_fallbacks.get(suite, global_fallback))
            suite_estimates = estimates_by_suite[suite]
            overhead = setup_seconds[suite]
            legacy = legacy_round_robin(hashes, legacy_shard_count)
            units, weights = expand_runtime_units(
                hashes,
                suite_estimates,
                fallback,
                target_shard_seconds=target_shard_seconds,
                setup_seconds=overhead,
                test_item_counts=test_item_counts[suite],
                maximum_slices_per_hash=maximum_slices_per_hash,
            )
            balanced = weighted_runtime_lpt(units, balanced_shard_counts[suite], weights)
            stage = suite_stage(suite)
            legacy_stages[stage] = max(legacy_stages[stage], predicted_makespan(legacy, suite_estimates, fallback))
            balanced_stages[stage] = max(balanced_stages[stage], predicted_makespan(balanced, weights, fallback))
            legacy_runner_seconds += sum(float(suite_estimates.get(riot_hash, fallback)) for riot_hash in hashes)
            balanced_runner_seconds += sum(weights.values())
        results.append(
            (
                shape.cohort,
                max(legacy_stages.values()),
                max(balanced_stages.values()),
                legacy_runner_seconds,
                balanced_runner_seconds,
            )
        )

    if not results:
        raise AllocationError("PR replay contains no modeled semantic suites")

    def summarize(items: list[tuple[str, float, float, float, float]]) -> dict[str, t.Any]:
        legacy = [item[1] for item in items]
        balanced = [item[2] for item in items]
        return {
            "count": len(items),
            "legacy": {
                "median_seconds": round(percentile(legacy, 0.5), 6),
                "p75_seconds": round(percentile(legacy, 0.75), 6),
                "p90_seconds": round(percentile(legacy, 0.9), 6),
            },
            "balanced": {
                "median_seconds": round(percentile(balanced, 0.5), 6),
                "p75_seconds": round(percentile(balanced, 0.75), 6),
                "p90_seconds": round(percentile(balanced, 0.9), 6),
            },
            "legacy_runner_seconds": round(sum(item[3] for item in items), 6),
            "balanced_runner_seconds": round(sum(item[4] for item in items), 6),
        }

    overall = summarize(results)
    overall["median_improvement_ratio"] = round(
        1 - overall["balanced"]["median_seconds"] / overall["legacy"]["median_seconds"], 6
    )
    overall["runner_seconds_change_ratio"] = round(
        overall["balanced_runner_seconds"] / overall["legacy_runner_seconds"] - 1, 6
    )
    by_cohort: dict[str, list[tuple[str, float, float, float, float]]] = defaultdict(list)
    for item in results:
        by_cohort[item[0]].append(item)
    return {
        "schema_version": 1,
        "kind": "historical-pr-shape-replay",
        "dataset_fingerprint_sha256": _fingerprint(shapes),
        "input_shape_count": len(shapes),
        "modeled_shape_count": len(results),
        "unmodeled_shape_count": unmodeled_shapes,
        "target_jobs": target_jobs,
        "target_shard_seconds": target_shard_seconds,
        "overall": overall,
        "cohorts": {cohort: summarize(items) for cohort, items in sorted(by_cohort.items())},
    }
