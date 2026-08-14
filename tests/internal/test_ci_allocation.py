"""Tests for duration-aware CI allocation contracts."""

from dataclasses import replace
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import json

import pytest

from scripts.ci_allocation.history import Observation
from scripts.ci_allocation.history import build_runtime_model
from scripts.ci_allocation.history import live_shadow_report
from scripts.ci_allocation.history import observation_from_datadog
from scripts.ci_allocation.history import ratchet_violations
from scripts.ci_allocation.history import replay_observations
from scripts.ci_allocation.jobs import JobObservation
from scripts.ci_allocation.jobs import job_from_datadog
from scripts.ci_allocation.junit import verify_junit_parity
from scripts.ci_allocation.manifest import build_allocation_manifest
from scripts.ci_allocation.manifest import verify_allocation_manifest
from scripts.ci_allocation.planner import AllocationError
from scripts.ci_allocation.planner import legacy_round_robin
from scripts.ci_allocation.planner import predicted_makespan
from scripts.ci_allocation.planner import verify_assignments
from scripts.ci_allocation.planner import weighted_lpt
from scripts.ci_allocation.suites import SuiteVenvInfo
from scripts.ci_allocation.suites import compute_runtime_parallelism


def test_legacy_round_robin_preserves_current_assignment():
    assert legacy_round_robin(["f", "a", "e", "c", "b", "d"], 3) == [
        ["a", "d"],
        ["b", "e"],
        ["c", "f"],
    ]


def test_weighted_lpt_is_exact_and_reduces_the_predicted_long_pole():
    hashes = ["h1", "h2", "h3", "h4"]
    estimates = {"h1": 10, "h2": 9, "h3": 8, "h4": 1}
    legacy = legacy_round_robin(hashes, 2)
    balanced = weighted_lpt(hashes, 2, estimates, 60)

    verify_assignments(hashes, balanced)
    assert predicted_makespan(balanced, estimates, 60) < predicted_makespan(legacy, estimates, 60)


def test_assignment_verifier_fails_on_overlap():
    with pytest.raises(AllocationError, match="more than one shard"):
        verify_assignments(["a", "b"], [["a"], ["a", "b"]])


def test_runtime_parallelism_targets_modeled_shard_duration():
    suites = {"core": SuiteVenvInfo(("a", "b", "c", "d"), frozenset({"3.13"}))}

    result = compute_runtime_parallelism(
        suites,
        ["core"],
        {"a": 100, "b": 100, "c": 100, "d": 100},
        {},
        60,
        target_shard_seconds=150,
        maximum_parallelism_per_suite=2,
    )

    assert result == {"core": 2}


def test_runtime_parallelism_reallocates_within_legacy_job_budget():
    suites = {
        "long": SuiteVenvInfo(("a", "b", "c", "d"), frozenset({"3.13"})),
        "short": SuiteVenvInfo(("e", "f", "g", "h"), frozenset({"3.13"})),
    }

    result = compute_runtime_parallelism(
        suites,
        suites,
        {"a": 100, "b": 100, "c": 100, "d": 100, "e": 40, "f": 40, "g": 40, "h": 40},
        {},
        60,
        target_shard_seconds=150,
        maximum_parallelism_per_suite=4,
        maximum_total_jobs=4,
    )

    assert result == {"long": 2, "short": 2}


def test_datadog_session_normalization_uses_riot_hash_as_atomic_identity():
    event = {
        "attributes": {
            "attributes": {
                "duration": 12_500_000_000,
                "start": 1_800_000_000_000_000_000,
                "test": {"status": "pass", "configuration": {"riot_hash": "abc123"}},
                "ci": {
                    "pipeline": {"id": "pipeline-1"},
                    "stage": {"name": "appsec"},
                    "job": {"name": "appsec/threats 4/6"},
                },
                "git": {"commit": {"sha": "deadbeef"}},
            }
        }
    }

    observation = observation_from_datadog(event)

    assert observation.riot_hash == "abc123"
    assert observation.suite == "appsec::threats"
    assert observation.shard_total == 6
    assert observation.duration_seconds == 12.5


def test_datadog_job_normalization_captures_total_and_queue_time():
    event = {
        "attributes": {
            "attributes": {
                "start": "2026-01-01T00:00:00Z",
                "duration_seconds": 90,
                "queue_seconds": 5,
                "test": {"configuration": {"ci_allocation_strategy": "balanced"}},
                "ci": {
                    "status": "success",
                    "pipeline": {"id": "pipeline-1"},
                    "stage": {"name": "core"},
                    "job": {"id": "job-1", "name": "core/internal 2/4"},
                },
            }
        }
    }

    observation = job_from_datadog(event)

    assert observation.suite == "internal"
    assert observation.shard_index == 2
    assert observation.duration_seconds == 90
    assert observation.queue_seconds == 5
    assert observation.strategy == "balanced"


def test_datadog_job_normalization_rejects_missing_queue_time():
    event = {
        "attributes": {
            "attributes": {
                "start": "2026-01-01T00:00:00Z",
                "duration_seconds": 90,
                "ci": {
                    "status": "success",
                    "pipeline": {"id": "pipeline-1"},
                    "stage": {"name": "core"},
                    "job": {"id": "job-1", "name": "core/internal 2/4"},
                },
            }
        }
    }

    with pytest.raises(AllocationError, match="queue_seconds must be numeric"):
        job_from_datadog(event)


def _observation(
    riot_hash: str,
    duration: float,
    when: datetime,
    pipeline: str,
    *,
    shard_total: int = 2,
) -> Observation:
    return Observation(
        riot_hash=riot_hash,
        suite="core",
        duration_seconds=duration,
        timestamp=when.isoformat().replace("+00:00", "Z"),
        status="pass",
        pipeline_id=pipeline,
        commit_sha=pipeline,
        shard_index=1,
        shard_total=shard_total,
        job_name=f"core {pipeline}",
    )


def test_runtime_model_reserves_holdout_and_replays_observed_durations():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    durations = {"h1": 10, "h2": 9, "h3": 8, "h4": 1}
    observations = []
    for day in (0, 1, 2):
        observations.extend(
            _observation(riot_hash, duration, start + timedelta(days=day), f"train-{day}")
            for riot_hash, duration in durations.items()
        )
    for day in (25, 26):
        observations.extend(
            _observation(riot_hash, duration, start + timedelta(days=day), f"holdout-{day}")
            for riot_hash, duration in durations.items()
        )
    observations.append(replace(observations[-1], status="fail", riot_hash="failed-hash"))
    jobs = [
        JobObservation(
            pipeline_id=f"train-{day}",
            job_id=f"job-{day}",
            job_name=f"core train-{day}",
            stage_name="core",
            suite="core",
            strategy="legacy",
            shard_index=1,
            shard_total=2,
            duration_seconds=sum(durations.values()) + 4,
            queue_seconds=2,
            status="success",
            timestamp=(start + timedelta(days=day)).isoformat().replace("+00:00", "Z"),
        )
        for day in (0, 1, 2)
    ]

    model = build_runtime_model(
        observations,
        {
            "estimate_quantile": 0.9,
            "half_life_days": 30,
            "history_window_days": 90,
            "holdout_days": 7,
            "minimum_samples": 2,
            "sparse_safety_factor": 1.25,
        },
        jobs,
    )
    report = replay_observations(
        observations,
        model,
        target_shard_seconds=12,
        maximum_parallelism_per_suite=3,
    )

    assert model["dataset"]["training_observations"] == 12
    assert model["dataset"]["holdout_observations"] == 8
    assert model["dataset"]["censored_observations"] == 1
    assert model["overheads"]["global_seconds"] == 0
    assert model["overheads"]["unit_global_seconds"] == 1
    assert model["overheads"]["matched_session_count"] == 12
    assert model["estimates"]["h1"] == 11
    assert report["pipeline_count"] == 2
    assert report["balanced"]["median_seconds"] < report["legacy"]["median_seconds"]


def test_runtime_model_rejects_session_only_timings():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    observations = [
        _observation("h1", 10, start, "train"),
        _observation("h1", 10, start + timedelta(days=2), "holdout"),
    ]

    with pytest.raises(AllocationError, match="missing CI job timing"):
        build_runtime_model(
            observations,
            {
                "estimate_quantile": 0.9,
                "half_life_days": 30,
                "history_window_days": 90,
                "holdout_days": 1,
                "minimum_samples": 1,
                "sparse_safety_factor": 1.25,
            },
            [],
        )


def test_manifest_proves_both_topologies_cover_the_same_suite():
    model = {
        "schema_version": 1,
        "planner_version": "weighted-lpt-v1",
        "dataset": {"source": "test"},
        "parameters": {},
        "overheads": {
            "global_seconds": 0,
            "suite_seconds": {},
            "unit_global_seconds": 1,
            "unit_suite_seconds": {},
            "sample_count": 1,
            "matched_session_count": 2,
        },
        "fallbacks": {"global_seconds": 60, "suite_seconds": {}},
        "estimates": {"a": 10, "b": 5},
    }
    manifest = build_allocation_manifest(
        suite_venv_info={"core": SuiteVenvInfo(("a", "b"), frozenset({"3.13"}))},
        suite_configs={"core": {"pattern": "core"}},
        legacy_shard_counts={"core": 1},
        balanced_shard_counts={"core": 2},
        runtime_model=model,
        active_strategy="legacy",
    )

    verify_allocation_manifest(manifest)
    assert manifest["suites"][0]["legacy"]["shard_count"] == 1
    assert manifest["suites"][0]["balanced"]["shard_count"] == 2

    tampered = json.loads(json.dumps(manifest))
    tampered["suites"][0]["balanced"]["assignments"][0].append("b")
    with pytest.raises(AllocationError, match="more than one shard"):
        verify_allocation_manifest(tampered)


def test_live_shadow_report_requires_exact_hash_parity_and_compares_actual_shards():
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    observations = []
    for strategy, durations in (("legacy", {"a": 10, "b": 9, "c": 8}), ("balanced", {"a": 10, "b": 9, "c": 8})):
        for shard_index, (riot_hash, duration) in enumerate(durations.items(), 1):
            observation = _observation(riot_hash, duration, when, "pipeline", shard_total=3)
            observations.append(
                Observation(
                    **{
                        **observation.__dict__,
                        "shard_index": shard_index,
                        "strategy": strategy,
                        "job_name": f"core/core{'-allocation-shadow' if strategy == 'balanced' else ''} "
                        f"{shard_index}/3",
                    }
                )
            )

    jobs = []
    for strategy, durations in (("legacy", (18, 10, 8)), ("balanced", (10, 9, 8))):
        for shard_index, duration in enumerate(durations, 1):
            suffix = "-allocation-shadow" if strategy == "balanced" else ""
            jobs.append(
                JobObservation(
                    pipeline_id="pipeline",
                    job_id=f"{strategy}-{shard_index}",
                    job_name=f"core/core{suffix} {shard_index}/3",
                    stage_name="core",
                    suite="core",
                    strategy=strategy,
                    shard_index=shard_index,
                    shard_total=3,
                    duration_seconds=duration,
                    queue_seconds=1,
                    status="success",
                    timestamp=when.isoformat().replace("+00:00", "Z"),
                )
            )

    report = live_shadow_report(observations, jobs)

    assert report["pipeline_count"] == 1
    assert report["exact_hash_parity"] is True
    assert report["timing_source"] == "ci-jobs"
    assert report["balanced"]["median_seconds"] < report["legacy"]["median_seconds"]

    unresolved_jobs = [replace(job, strategy="unknown") for job in jobs]
    assert live_shadow_report(observations, unresolved_jobs)["timing_source"] == "ci-jobs"

    with pytest.raises(AllocationError, match="CI job timings are missing shards"):
        live_shadow_report(observations, jobs[:-1])

    observations[-1] = Observation(**{**observations[-1].__dict__, "riot_hash": "different"})
    with pytest.raises(AllocationError, match="hash parity failed"):
        live_shadow_report(observations)


def test_junit_parity_compares_test_multisets_and_execution_metadata(tmp_path):
    template = """\
<testsuites><testsuite name="suite"><properties>
<property name="riot.hash" value="abc"/>
<property name="riot.ci.allocation_strategy" value="{strategy}"/>
<property name="riot.python.version" value="3.13"/>
</properties><testcase classname="tests.test_module" name="test_value" file="tests/test_module.py"/>
</testsuite></testsuites>
"""
    legacy = tmp_path / "junit.legacy.abc.100.xml"
    balanced = tmp_path / "junit.balanced.abc.200.xml"
    legacy.write_text(template.format(strategy="legacy"))
    balanced.write_text(template.format(strategy="balanced"))

    report = verify_junit_parity([legacy], [balanced])

    assert report["test_identity_count"] == 1
    assert report["execution_metadata_parity"] is True

    legacy.write_text(template.format(strategy="legacy").replace('<property name="riot.hash" value="abc"/>', ""))
    balanced.write_text(template.format(strategy="balanced").replace('<property name="riot.hash" value="abc"/>', ""))
    assert verify_junit_parity([legacy], [balanced])["riot_hash_count"] == 1

    without_metadata = template.format(strategy="{strategy}").replace(
        '<property name="riot.python.version" value="3.13"/>', ""
    )
    legacy.write_text(without_metadata.format(strategy="legacy"))
    balanced.write_text(without_metadata.format(strategy="balanced"))
    with pytest.raises(AllocationError, match="no Riot execution metadata evidence"):
        verify_junit_parity([legacy], [balanced])

    digest = "a" * 64
    fallback_legacy = tmp_path / f"junit.legacy.abc.{digest}.300.xml"
    fallback_balanced = tmp_path / f"junit.balanced.abc.{digest}.400.xml"
    fallback_legacy.write_text(without_metadata.format(strategy="legacy"))
    fallback_balanced.write_text(without_metadata.format(strategy="balanced"))
    assert verify_junit_parity([fallback_legacy], [fallback_balanced])["execution_metadata_parity"] is True

    legacy.write_text(template.format(strategy="legacy"))
    balanced.write_text(template.format(strategy="balanced").replace("test_value", "test_other"))
    with pytest.raises(AllocationError, match="test identity parity failed"):
        verify_junit_parity([legacy], [balanced])


def test_live_ratchet_requires_real_job_timing_and_exact_hash_parity():
    report = {
        "kind": "live-shadow-replay",
        "pipeline_count": 15,
        "legacy": {"median_seconds": 100, "p75_seconds": 120, "p90_seconds": 130},
        "balanced": {"median_seconds": 80, "p75_seconds": 100, "p90_seconds": 130},
        "median_improvement_ratio": 0.2,
        "runner_seconds_change_ratio": 0.01,
        "clean_success_rate_change": 0,
        "retry_rate_change": 0,
        "exact_hash_parity": True,
        "timing_source": "ci-jobs",
        "queue": {"legacy": {"p90_seconds": 5}, "balanced": {"p90_seconds": 5}},
    }
    policy = {
        "live_shadow": {
            "minimum_runs": 15,
            "minimum_median_improvement_ratio": 0.15,
            "maximum_runner_seconds_increase_ratio": 0.05,
            "maximum_queue_p90_increase_ratio": 0.05,
        }
    }

    assert ratchet_violations(report, policy) == []

    report["exact_hash_parity"] = False
    assert "live shadow Riot hash parity was not proven" in ratchet_violations(report, policy)
