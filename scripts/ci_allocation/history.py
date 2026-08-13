"""Normalize historical CI data and build a time-decayed runtime model."""

from __future__ import annotations

from collections import Counter
from collections import defaultdict
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import hashlib
import json
import math
from pathlib import Path
import re
import typing as t

from .planner import AllocationError
from .planner import legacy_round_robin
from .planner import weighted_lpt


@dataclass(frozen=True)
class Observation:
    riot_hash: str
    suite: str
    duration_seconds: float
    timestamp: str
    status: str
    pipeline_id: str
    commit_sha: str
    shard_index: int
    shard_total: int
    job_name: str
    strategy: str = "legacy"


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_from_nanoseconds(value: t.Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AllocationError("Datadog test session start must be a nanosecond timestamp")
    return datetime.fromtimestamp(float(value) / 1_000_000_000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def suite_from_job_name(job_name: str, stage_name: str) -> tuple[str, int, int]:
    """Recover the suitespec identity and shard count from a generated GitLab job."""
    match = re.search(r"\s+(\d+)/(\d+)$", job_name)
    shard_index = int(match.group(1)) if match else 1
    shard_total = int(match.group(2)) if match else 1
    base = job_name[: match.start()] if match else job_name
    base = base.removesuffix("-allocation-shadow")
    prefix = f"{stage_name}/"
    clean_name = base[len(prefix) :] if stage_name and base.startswith(prefix) else base
    suite = clean_name if stage_name in {"", "core"} else f"{stage_name}::{clean_name}"
    return suite, shard_index, shard_total


def suite_stage(suite: str) -> str:
    return suite.split("::", 1)[0] if "::" in suite else "core"


def observation_from_datadog(event: t.Mapping[str, t.Any]) -> Observation:
    """Normalize one Datadog test-session event exported from CI Visibility."""
    outer_attributes = event.get("attributes")
    if not isinstance(outer_attributes, dict):
        raise AllocationError("Datadog event is missing attributes")
    attributes = outer_attributes.get("attributes", outer_attributes)
    if not isinstance(attributes, dict):
        raise AllocationError("Datadog event attributes are malformed")

    test = attributes.get("test")
    ci = attributes.get("ci")
    git = attributes.get("git")
    if not isinstance(test, dict) or not isinstance(ci, dict) or not isinstance(git, dict):
        raise AllocationError("Datadog event is missing test, ci, or git metadata")
    configuration = test.get("configuration")
    pipeline = ci.get("pipeline")
    job = ci.get("job")
    stage = ci.get("stage")
    commit = git.get("commit")
    if (
        not isinstance(configuration, dict)
        or not isinstance(pipeline, dict)
        or not isinstance(job, dict)
        or not isinstance(stage, dict)
        or not isinstance(commit, dict)
    ):
        raise AllocationError("Datadog event is missing configuration or CI identity")

    riot_hash = configuration.get("riot_hash")
    job_name = job.get("name")
    stage_name = stage.get("name")
    pipeline_id = pipeline.get("id")
    duration = attributes.get("duration")
    if not isinstance(riot_hash, str) or not riot_hash:
        raise AllocationError("Datadog event is missing test.configuration.riot_hash")
    if not isinstance(job_name, str) or not isinstance(stage_name, str):
        raise AllocationError("Datadog event is missing its job or stage name")
    if not isinstance(pipeline_id, (str, int)) or not str(pipeline_id):
        raise AllocationError("Datadog event is missing its pipeline identity")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
        raise AllocationError("Datadog event duration must be positive nanoseconds")

    suite, shard_index, shard_total = suite_from_job_name(job_name, stage_name)
    strategy = configuration.get("ci_allocation_strategy", "legacy")
    if strategy not in {"legacy", "balanced"}:
        raise AllocationError("Datadog event has an invalid CI allocation strategy")
    return Observation(
        riot_hash=riot_hash,
        suite=suite,
        duration_seconds=float(duration) / 1_000_000_000,
        timestamp=_iso_from_nanoseconds(attributes.get("start")),
        status=str(test.get("status", "unknown")),
        pipeline_id=str(pipeline_id),
        commit_sha=str(commit.get("sha", "")),
        shard_index=shard_index,
        shard_total=shard_total,
        job_name=job_name,
        strategy=strategy,
    )


def load_json_documents(path: Path) -> list[t.Mapping[str, t.Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        value = value["data"]
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise AllocationError("history input must be a JSON object, array, API data response, or JSONL")
    return value


def load_observations(path: Path) -> list[Observation]:
    observations = []
    for item in load_json_documents(path):
        if item.get("schema_version") == 1 and "riot_hash" in item:
            normalized = dict(item)
            normalized.setdefault("shard_index", 1)
            normalized.setdefault("strategy", "legacy")
            observations.append(Observation(**{key: normalized[key] for key in Observation.__dataclass_fields__}))
        else:
            observations.append(observation_from_datadog(item))
    return observations


def write_observations(path: Path, observations: t.Iterable[Observation]) -> None:
    lines = [json.dumps({"schema_version": 1, **asdict(item)}, sort_keys=True) for item in observations]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _weighted_quantile(values: list[tuple[float, float]], quantile: float) -> float:
    if not values:
        raise AllocationError("cannot estimate a duration from no observations")
    if not 0 < quantile <= 1:
        raise AllocationError("duration quantile must be in (0, 1]")
    ordered = sorted(values)
    threshold = sum(weight for _, weight in ordered) * quantile
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def _fingerprint(observations: t.Iterable[Observation]) -> str:
    normalized = [asdict(item) for item in observations]
    normalized.sort(key=lambda item: tuple(str(item[key]) for key in sorted(item)))
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _estimate(observations: list[Observation], reference: datetime, half_life_days: float, quantile: float) -> float:
    weighted = []
    for item in observations:
        age_days = max(0.0, (reference - _parse_timestamp(item.timestamp)).total_seconds() / 86400)
        weight = 0.5 ** (age_days / half_life_days)
        weighted.append((item.duration_seconds, weight))
    return _weighted_quantile(weighted, quantile)


def _estimate_timed_values(
    values: list[tuple[float, str]], reference: datetime, half_life_days: float, quantile: float
) -> float:
    weighted = []
    for value, timestamp in values:
        age_days = max(0.0, (reference - _parse_timestamp(timestamp)).total_seconds() / 86400)
        weighted.append((value, 0.5 ** (age_days / half_life_days)))
    return _weighted_quantile(weighted, quantile)


def build_runtime_model(
    observations: list[Observation],
    policy: t.Mapping[str, t.Any],
    job_observations: t.Optional[list[t.Any]] = None,
) -> dict[str, t.Any]:
    """Build estimates from a recent training window while reserving the newest holdout."""
    if not observations:
        raise AllocationError("runtime modeling requires observations")
    latest = max(_parse_timestamp(item.timestamp) for item in observations)
    history_days = int(policy["history_window_days"])
    earliest = latest - timedelta(days=history_days)
    in_window_all = [item for item in observations if _parse_timestamp(item.timestamp) >= earliest]
    successful = [item for item in in_window_all if item.status == "pass"]
    if not successful:
        raise AllocationError("runtime modeling requires at least one passing observation")

    holdout_days = int(policy["holdout_days"])
    half_life_days = float(policy["half_life_days"])
    quantile = float(policy["estimate_quantile"])
    minimum_samples = int(policy["minimum_samples"])
    sparse_safety_factor = float(policy["sparse_safety_factor"])
    if min(history_days, half_life_days, minimum_samples, sparse_safety_factor) <= 0 or holdout_days < 0:
        raise AllocationError("runtime model policy values must be positive")

    holdout_start = latest - timedelta(days=holdout_days)
    training = [item for item in successful if _parse_timestamp(item.timestamp) < holdout_start]
    holdout = [item for item in successful if _parse_timestamp(item.timestamp) >= holdout_start]
    if not training:
        raise AllocationError("runtime model training window is empty")

    by_hash: dict[str, list[Observation]] = defaultdict(list)
    by_suite: dict[str, list[Observation]] = defaultdict(list)
    for item in training:
        by_hash[item.riot_hash].append(item)
        by_suite[item.suite].append(item)

    global_seconds = _estimate(training, holdout_start, half_life_days, quantile)
    suite_seconds = {
        suite: _estimate(items, holdout_start, half_life_days, quantile) for suite, items in sorted(by_suite.items())
    }
    estimates: dict[str, dict[str, t.Any]] = {}
    for riot_hash, items in sorted(by_hash.items()):
        observed = _estimate(items, holdout_start, half_life_days, quantile)
        suite_counts = Counter(item.suite for item in items)
        suite = min(suite_counts, key=lambda value: (-suite_counts[value], value))
        if len(items) >= minimum_samples:
            estimate_seconds = observed
            source = "observed"
        else:
            estimate_seconds = max(observed, suite_seconds.get(suite, global_seconds)) * sparse_safety_factor
            source = "sparse-fallback"
        estimates[riot_hash] = {
            "estimate_seconds": round(estimate_seconds, 6),
            "observed_quantile_seconds": round(observed, 6),
            "sample_count": len(items),
            "source": source,
            "suite": suite,
        }

    session_seconds: dict[tuple[str, str], float] = defaultdict(float)
    for item in training:
        session_seconds[(item.pipeline_id, item.job_name)] += item.duration_seconds
    overhead_values: list[tuple[float, str]] = []
    queue_values: list[tuple[float, str]] = []
    overhead_by_suite: dict[str, list[tuple[float, str]]] = defaultdict(list)
    window_jobs = [job for job in job_observations or [] if earliest <= _parse_timestamp(job.timestamp) <= latest]
    for job in window_jobs:
        if job.strategy != "legacy" or job.status not in {"pass", "success"}:
            continue
        if _parse_timestamp(job.timestamp) >= holdout_start:
            continue
        session_duration = session_seconds.get((job.pipeline_id, job.job_name))
        if session_duration is None:
            continue
        overhead = max(0.0, job.duration_seconds - session_duration)
        value = (overhead, job.timestamp)
        overhead_values.append(value)
        overhead_by_suite[job.suite].append(value)
        queue_values.append((job.queue_seconds, job.timestamp))
    global_overhead = (
        _estimate_timed_values(overhead_values, holdout_start, half_life_days, quantile) if overhead_values else 0.0
    )
    suite_overheads = {
        suite: _estimate_timed_values(items, holdout_start, half_life_days, quantile)
        for suite, items in sorted(overhead_by_suite.items())
    }
    queue_seconds = (
        _estimate_timed_values(queue_values, holdout_start, half_life_days, quantile) if queue_values else 0.0
    )
    normalized_jobs = [asdict(job) for job in window_jobs]
    normalized_jobs.sort(key=lambda item: tuple(str(item[key]) for key in sorted(item)))
    job_fingerprint = (
        hashlib.sha256(json.dumps(normalized_jobs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if normalized_jobs
        else None
    )

    return {
        "schema_version": 1,
        "planner_version": "weighted-lpt-v1",
        "generated_at": latest.isoformat().replace("+00:00", "Z"),
        "dataset": {
            "source": "datadog-test-visibility-export",
            "fingerprint_sha256": _fingerprint(in_window_all),
            "job_fingerprint_sha256": job_fingerprint,
            "history_window_days": history_days,
            "holdout_days": holdout_days,
            "training_observations": len(training),
            "holdout_observations": len(holdout),
            "job_observations": len(normalized_jobs),
            "censored_observations": len(in_window_all) - len(successful),
            "status_counts": dict(sorted(Counter(item.status for item in in_window_all).items())),
            "training_end": holdout_start.isoformat().replace("+00:00", "Z"),
            "window_end": latest.isoformat().replace("+00:00", "Z"),
        },
        "parameters": {
            "estimate_quantile": quantile,
            "half_life_days": half_life_days,
            "history_window_days": history_days,
            "holdout_days": holdout_days,
            "minimum_samples": minimum_samples,
            "sparse_safety_factor": sparse_safety_factor,
        },
        "fallbacks": {
            "global_seconds": round(global_seconds, 6),
            "suite_seconds": {suite: round(value, 6) for suite, value in suite_seconds.items()},
        },
        "overheads": {
            "global_seconds": round(global_overhead, 6),
            "suite_seconds": {suite: round(value, 6) for suite, value in suite_overheads.items()},
            "queue_p90_seconds": round(queue_seconds, 6),
            "sample_count": len(overhead_values),
        },
        "estimates": estimates,
    }


def validate_runtime_model(model: t.Mapping[str, t.Any]) -> None:
    if model.get("schema_version") != 1 or model.get("planner_version") != "weighted-lpt-v1":
        raise AllocationError("unsupported runtime model schema or planner version")
    fallbacks = model.get("fallbacks")
    estimates = model.get("estimates")
    dataset = model.get("dataset")
    parameters = model.get("parameters")
    overheads = model.get("overheads")
    if (
        not isinstance(fallbacks, dict)
        or not isinstance(estimates, dict)
        or not isinstance(dataset, dict)
        or not isinstance(parameters, dict)
        or not isinstance(overheads, dict)
    ):
        raise AllocationError("runtime model is missing dataset, parameters, fallbacks, or estimates")
    fallback = fallbacks.get("global_seconds")
    if isinstance(fallback, bool) or not isinstance(fallback, (int, float)) or fallback <= 0:
        raise AllocationError("runtime model global fallback must be positive")
    overhead = overheads.get("global_seconds")
    if isinstance(overhead, bool) or not isinstance(overhead, (int, float)) or overhead < 0:
        raise AllocationError("runtime model global overhead cannot be negative")
    for riot_hash, item in estimates.items():
        if not isinstance(riot_hash, str) or not isinstance(item, dict):
            raise AllocationError("runtime model estimates are malformed")
        estimate = item.get("estimate_seconds")
        if isinstance(estimate, bool) or not isinstance(estimate, (int, float)) or estimate <= 0:
            raise AllocationError(f"runtime model estimate is invalid for {riot_hash}")


def runtime_estimates(model: t.Mapping[str, t.Any]) -> tuple[dict[str, float], float]:
    validate_runtime_model(model)
    estimates = {riot_hash: float(item["estimate_seconds"]) for riot_hash, item in model["estimates"].items()}
    return estimates, float(model["fallbacks"]["global_seconds"])


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise AllocationError("cannot calculate a percentile from no values")
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


def replay_observations(
    observations: list[Observation],
    model: t.Mapping[str, t.Any],
    *,
    holdout_only: bool = True,
    target_shard_seconds: t.Optional[float] = None,
    maximum_parallelism_per_suite: int = 25,
) -> dict[str, t.Any]:
    """Replay legacy and balanced assignments against observed execution durations."""
    validate_runtime_model(model)
    estimates, fallback_seconds = runtime_estimates(model)
    suite_overheads = model["overheads"].get("suite_seconds", {})
    global_overhead = float(model["overheads"]["global_seconds"])
    selected = [item for item in observations if item.status == "pass"]
    training_end = model["dataset"].get("training_end")
    if holdout_only and training_end:
        cutoff = _parse_timestamp(str(training_end))
        selected = [item for item in selected if _parse_timestamp(item.timestamp) >= cutoff]
    if not selected:
        raise AllocationError("replay dataset is empty")

    grouped: dict[tuple[str, str, int], dict[str, float]] = defaultdict(dict)
    for item in selected:
        key = (item.pipeline_id, item.suite, item.shard_total)
        grouped[key][item.riot_hash] = max(grouped[key].get(item.riot_hash, 0.0), item.duration_seconds)

    stage_legacy: dict[tuple[str, str], float] = defaultdict(float)
    stage_balanced: dict[tuple[str, str], float] = defaultdict(float)
    runner_seconds: dict[str, float] = defaultdict(float)
    legacy_runner_seconds: dict[str, float] = defaultdict(float)
    balanced_runner_seconds: dict[str, float] = defaultdict(float)
    for (pipeline_id, suite, shard_total), durations in grouped.items():
        shard_count = min(shard_total, len(durations))
        hashes = sorted(durations)
        overhead = float(suite_overheads.get(suite, global_overhead))
        legacy = legacy_round_robin(hashes, shard_count)
        balanced_shard_count = shard_count
        if target_shard_seconds is not None:
            if target_shard_seconds <= 0 or maximum_parallelism_per_suite <= 0:
                raise AllocationError("historical replay shard policy must be positive")
            modeled_work = sum(float(estimates.get(item, fallback_seconds)) for item in hashes)
            available_seconds = max(1.0, target_shard_seconds - overhead)
            balanced_shard_count = min(
                len(hashes),
                maximum_parallelism_per_suite,
                max(1, math.ceil(modeled_work / available_seconds)),
            )
        balanced = weighted_lpt(hashes, balanced_shard_count, estimates, fallback_seconds)
        legacy_makespan = max(overhead + sum(durations[item] for item in shard) for shard in legacy)
        balanced_makespan = max(overhead + sum(durations[item] for item in shard) for shard in balanced)
        stage_key = (pipeline_id, suite_stage(suite))
        stage_legacy[stage_key] = max(stage_legacy[stage_key], legacy_makespan)
        stage_balanced[stage_key] = max(stage_balanced[stage_key], balanced_makespan)
        runner_seconds[pipeline_id] += sum(durations.values())
        legacy_runner_seconds[pipeline_id] += sum(durations.values()) + overhead * len(legacy)
        balanced_runner_seconds[pipeline_id] += sum(durations.values()) + overhead * len(balanced)

    pipeline_legacy: dict[str, float] = defaultdict(float)
    pipeline_balanced: dict[str, float] = defaultdict(float)
    for (pipeline_id, _stage), seconds in stage_legacy.items():
        pipeline_legacy[pipeline_id] += seconds
    for (pipeline_id, _stage), seconds in stage_balanced.items():
        pipeline_balanced[pipeline_id] += seconds
    legacy_values = list(pipeline_legacy.values())
    balanced_values = [pipeline_balanced[pipeline_id] for pipeline_id in pipeline_legacy]

    def summary(values: list[float]) -> dict[str, float]:
        return {
            "median_seconds": round(percentile(values, 0.5), 6),
            "p75_seconds": round(percentile(values, 0.75), 6),
            "p90_seconds": round(percentile(values, 0.9), 6),
        }

    legacy_summary = summary(legacy_values)
    balanced_summary = summary(balanced_values)
    improvement = 1 - balanced_summary["median_seconds"] / legacy_summary["median_seconds"]
    legacy_runner_total = sum(legacy_runner_seconds.values())
    balanced_runner_total = sum(balanced_runner_seconds.values())
    return {
        "schema_version": 1,
        "kind": "historical-holdout-replay",
        "dataset_fingerprint_sha256": _fingerprint(selected),
        "pipeline_count": len(legacy_values),
        "observation_count": len(selected),
        "legacy": legacy_summary,
        "balanced": balanced_summary,
        "median_improvement_ratio": round(improvement, 6),
        "runner_seconds_change_ratio": round(balanced_runner_total / legacy_runner_total - 1, 6),
        "clean_success_rate_change": 0.0,
        "retry_rate_change": 0.0,
        "reliability_evidence": "censored-history-only",
        "test_execution_seconds": round(sum(runner_seconds.values()), 6),
        "legacy_runner_seconds": round(legacy_runner_total, 6),
        "balanced_runner_seconds": round(balanced_runner_total, 6),
    }


def live_shadow_report(
    observations: list[Observation], job_observations: t.Optional[list[t.Any]] = None
) -> dict[str, t.Any]:
    """Compare paired legacy and balanced executions from opt-in shadow pipelines."""
    if not observations:
        raise AllocationError("live shadow reporting requires observations")

    hash_sets: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    shard_seconds: dict[tuple[str, str, str, int], float] = defaultdict(float)
    pipeline_statuses: dict[tuple[str, str], list[str]] = defaultdict(list)
    execution_counts: Counter[tuple[str, str, str, str]] = Counter()
    for item in observations:
        hash_sets[(item.pipeline_id, item.suite, item.strategy)].add(item.riot_hash)
        shard_seconds[(item.pipeline_id, item.strategy, item.suite, item.shard_index)] += item.duration_seconds
        pipeline_statuses[(item.pipeline_id, item.strategy)].append(item.status)
        execution_counts[(item.pipeline_id, item.strategy, item.suite, item.riot_hash)] += 1

    pipeline_ids = sorted({item.pipeline_id for item in observations})
    paired = []
    for pipeline_id in pipeline_ids:
        strategies = {strategy for seen_pipeline, strategy in pipeline_statuses if seen_pipeline == pipeline_id}
        if strategies != {"legacy", "balanced"}:
            continue
        suites = {suite for seen_pipeline, suite, _strategy in hash_sets if seen_pipeline == pipeline_id}
        for suite in suites:
            if hash_sets.get((pipeline_id, suite, "legacy")) != hash_sets.get((pipeline_id, suite, "balanced")):
                raise AllocationError(f"live shadow hash parity failed for pipeline {pipeline_id}, suite {suite}")
        paired.append(pipeline_id)
    if not paired:
        raise AllocationError("live shadow data contains no paired pipelines")

    makespans: dict[str, list[float]] = {"legacy": [], "balanced": []}
    runner_seconds: dict[str, float] = defaultdict(float)
    clean_success: dict[str, int] = defaultdict(int)
    retries: dict[str, int] = defaultdict(int)
    queue_seconds: dict[str, list[float]] = defaultdict(list)
    if job_observations:
        relevant_suites = set(hash_sets)
        jobs = [
            job
            for job in job_observations
            if job.pipeline_id in paired and (job.pipeline_id, job.suite, job.strategy) in relevant_suites
        ]
        job_counts: Counter[tuple[str, str, str]] = Counter(
            (job.pipeline_id, job.strategy, job.job_name) for job in jobs
        )
        for pipeline_id in paired:
            for strategy in ("legacy", "balanced"):
                selected_jobs = [job for job in jobs if job.pipeline_id == pipeline_id and job.strategy == strategy]
                if not selected_jobs:
                    raise AllocationError(f"live shadow CI job timings are missing for {pipeline_id} {strategy}")
                stage_seconds: dict[str, float] = defaultdict(float)
                for job in selected_jobs:
                    stage_seconds[job.stage_name] = max(stage_seconds[job.stage_name], job.duration_seconds)
                    runner_seconds[strategy] += job.duration_seconds
                    queue_seconds[strategy].append(job.queue_seconds)
                makespans[strategy].append(sum(stage_seconds.values()))
                clean_success[strategy] += int(all(job.status in {"pass", "success"} for job in selected_jobs))
                retries[strategy] += sum(
                    max(0, count - 1)
                    for (seen_pipeline, seen_strategy, _job), count in job_counts.items()
                    if seen_pipeline == pipeline_id and seen_strategy == strategy
                )
        timing_source = "ci-jobs"
    else:
        for pipeline_id in paired:
            for strategy in ("legacy", "balanced"):
                relevant = [
                    seconds
                    for (seen_pipeline, seen_strategy, _suite, _shard), seconds in shard_seconds.items()
                    if seen_pipeline == pipeline_id and seen_strategy == strategy
                ]
                makespans[strategy].append(max(relevant))
                runner_seconds[strategy] += sum(relevant)
                clean_success[strategy] += int(
                    all(status == "pass" for status in pipeline_statuses[(pipeline_id, strategy)])
                )
                retries[strategy] += sum(
                    max(0, count - 1)
                    for (seen_pipeline, seen_strategy, _suite, _hash), count in execution_counts.items()
                    if seen_pipeline == pipeline_id and seen_strategy == strategy
                )
        timing_source = "test-sessions"

    def summary(strategy: str) -> dict[str, float]:
        values = makespans[strategy]
        return {
            "median_seconds": round(percentile(values, 0.5), 6),
            "p75_seconds": round(percentile(values, 0.75), 6),
            "p90_seconds": round(percentile(values, 0.9), 6),
        }

    legacy_summary = summary("legacy")
    balanced_summary = summary("balanced")
    pipeline_count = len(paired)
    return {
        "schema_version": 1,
        "kind": "live-shadow-replay",
        "dataset_fingerprint_sha256": _fingerprint(item for item in observations if item.pipeline_id in set(paired)),
        "pipeline_count": pipeline_count,
        "legacy": legacy_summary,
        "balanced": balanced_summary,
        "median_improvement_ratio": round(1 - balanced_summary["median_seconds"] / legacy_summary["median_seconds"], 6),
        "runner_seconds_change_ratio": round(runner_seconds["balanced"] / runner_seconds["legacy"] - 1, 6),
        "clean_success_rate_change": round(
            clean_success["balanced"] / pipeline_count - clean_success["legacy"] / pipeline_count, 6
        ),
        "retry_rate_change": round(retries["balanced"] / pipeline_count - retries["legacy"] / pipeline_count, 6),
        "exact_hash_parity": True,
        "timing_source": timing_source,
        "queue": {
            strategy: {
                "p50_seconds": round(percentile(values, 0.5), 6),
                "p90_seconds": round(percentile(values, 0.9), 6),
            }
            for strategy, values in sorted(queue_seconds.items())
        },
    }


def ratchet_violations(report: t.Mapping[str, t.Any], policy: t.Mapping[str, t.Any]) -> list[str]:
    """Return reasons a historical or live candidate cannot be promoted."""
    kind = report.get("kind")
    if kind == "historical-pr-shape-replay":
        limits = policy["pr_shape_replay"]
        overall = report["overall"]
        violations = []
        if int(report.get("modeled_shape_count", 0)) < int(limits["minimum_shapes"]):
            violations.append("PR shape count is below the minimum")
        if float(overall.get("median_improvement_ratio", -1)) < float(limits["minimum_median_improvement_ratio"]):
            violations.append("PR median improvement is below the ratchet")
        if float(overall["balanced"]["p75_seconds"]) >= float(overall["legacy"]["p75_seconds"]):
            violations.append("PR p75 did not improve")
        if float(overall["balanced"]["p90_seconds"]) > float(overall["legacy"]["p90_seconds"]):
            violations.append("PR p90 regressed")
        if float(overall["runner_seconds_change_ratio"]) > float(limits["maximum_runner_seconds_increase_ratio"]):
            violations.append("PR runner seconds exceed the allowed increase")
        for cohort, summary in report["cohorts"].items():
            if float(summary["balanced"]["p90_seconds"]) > float(summary["legacy"]["p90_seconds"]):
                violations.append(f"PR cohort p90 regressed: {cohort}")
        return violations
    if kind == "historical-holdout-replay":
        limits = policy["historical_replay"]
    elif kind == "live-shadow-replay":
        limits = policy["live_shadow"]
    else:
        raise AllocationError(f"unsupported ratchet report kind: {kind}")

    violations = []
    if int(report.get("pipeline_count", 0)) < int(limits["minimum_runs"]):
        violations.append("sample count is below the minimum")
    if float(report.get("median_improvement_ratio", -1)) < float(limits["minimum_median_improvement_ratio"]):
        violations.append("median improvement is below the ratchet")
    if float(report["balanced"]["p75_seconds"]) >= float(report["legacy"]["p75_seconds"]):
        violations.append("p75 did not improve")
    if float(report["balanced"]["p90_seconds"]) > float(report["legacy"]["p90_seconds"]):
        violations.append("p90 regressed")
    if float(report.get("runner_seconds_change_ratio", 1)) > float(limits["maximum_runner_seconds_increase_ratio"]):
        violations.append("runner seconds exceed the allowed increase")
    if kind == "live-shadow-replay":
        if report.get("exact_hash_parity") is not True:
            violations.append("live shadow Riot hash parity was not proven")
        if report.get("timing_source") != "ci-jobs":
            violations.append("live shadow timing must come from CI job events")
        queue = report.get("queue", {})
        if isinstance(queue, dict) and "legacy" in queue and "balanced" in queue:
            legacy_queue = float(queue["legacy"]["p90_seconds"])
            balanced_queue = float(queue["balanced"]["p90_seconds"])
            maximum_queue = legacy_queue * (1 + float(limits["maximum_queue_p90_increase_ratio"]))
            if balanced_queue > maximum_queue:
                violations.append("queue p90 regressed beyond the allowed increase")
        else:
            violations.append("live shadow queue evidence is missing")
        if float(report.get("clean_success_rate_change", -1)) < 0:
            violations.append("clean success rate regressed")
        if float(report.get("retry_rate_change", 1)) > 0:
            violations.append("retry rate regressed")
    return violations


def load_json(path: Path) -> dict[str, t.Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AllocationError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: t.Mapping[str, t.Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
