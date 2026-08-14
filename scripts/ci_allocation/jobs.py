"""Normalize GitLab job timing events used by the CI allocation model."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
import json
from pathlib import Path
import typing as t

from .history import _iso_from_nanoseconds
from .history import load_json_documents
from .history import suite_from_job_name
from .planner import AllocationError


@dataclass(frozen=True)
class JobObservation:
    pipeline_id: str
    job_id: str
    job_name: str
    stage_name: str
    suite: str
    strategy: str
    shard_index: int
    shard_total: int
    duration_seconds: float
    queue_seconds: float
    status: str
    timestamp: str


def _duration_seconds(attributes: t.Mapping[str, t.Any], seconds_key: str, nanoseconds_key: str) -> float:
    seconds = attributes.get(seconds_key)
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
        return float(seconds)
    nanoseconds = attributes.get(nanoseconds_key)
    if isinstance(nanoseconds, (int, float)) and not isinstance(nanoseconds, bool):
        return float(nanoseconds) / 1_000_000_000
    raise AllocationError(f"CI job {seconds_key} must be numeric")


def _strategy(attributes: t.Mapping[str, t.Any], ci: t.Mapping[str, t.Any], job_name: str) -> str:
    test = attributes.get("test", {})
    configuration = test.get("configuration", {}) if isinstance(test, dict) else {}
    candidates = (
        attributes.get("ci_allocation_strategy"),
        attributes.get("test.configuration.ci_allocation_strategy"),
        ci.get("allocation_strategy"),
        configuration.get("ci_allocation_strategy") if isinstance(configuration, dict) else None,
    )
    for candidate in candidates:
        if candidate is None:
            continue
        if candidate not in {"legacy", "balanced"}:
            raise AllocationError("Datadog CI job event has an invalid allocation strategy")
        return str(candidate)
    if "-allocation-shadow" in job_name:
        return "balanced"
    # AIDEV-NOTE: A normal job name is not evidence of the legacy strategy after
    # promotion. The history layer resolves this value from matching test sessions.
    return "unknown"


def _timestamp(attributes: t.Mapping[str, t.Any]) -> str:
    start = attributes.get("start")
    if isinstance(start, str):
        parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return _iso_from_nanoseconds(start)


def job_from_datadog(event: t.Mapping[str, t.Any]) -> JobObservation:
    outer_attributes = event.get("attributes", event)
    if not isinstance(outer_attributes, dict):
        raise AllocationError("Datadog CI job event is missing attributes")
    attributes = outer_attributes.get("attributes", outer_attributes)
    if not isinstance(attributes, dict):
        raise AllocationError("Datadog CI job attributes are malformed")

    ci = attributes.get("ci", {})
    if not isinstance(ci, dict):
        raise AllocationError("Datadog CI job event has malformed CI metadata")
    pipeline = ci.get("pipeline", {})
    job = ci.get("job", {})
    stage = ci.get("stage", {})
    if not isinstance(pipeline, dict) or not isinstance(job, dict) or not isinstance(stage, dict):
        raise AllocationError("Datadog CI job event is missing pipeline, job, or stage metadata")

    pipeline_id = pipeline.get("id", attributes.get("pipeline_id", ""))
    job_id = job.get("id", attributes.get("job_id", ""))
    job_name = job.get("name", attributes.get("job_name"))
    stage_name = stage.get("name", attributes.get("stage_name", ""))
    if not isinstance(job_name, str) or not job_name:
        raise AllocationError("Datadog CI job event is missing its job name")
    if not isinstance(pipeline_id, (str, int)) or not str(pipeline_id):
        raise AllocationError("Datadog CI job event is missing its pipeline identity")
    if not isinstance(stage_name, str) or not stage_name:
        raise AllocationError("Datadog CI job event is missing its stage name")
    suite, shard_index, shard_total = suite_from_job_name(job_name, stage_name)
    strategy = _strategy(attributes, ci, job_name)
    duration = _duration_seconds(attributes, "duration_seconds", "duration")
    timing_attributes = dict(attributes)
    if "ci.queue_time" not in timing_attributes and "queue_time" in ci:
        timing_attributes["ci.queue_time"] = ci["queue_time"]
    queue = _duration_seconds(timing_attributes, "queue_seconds", "ci.queue_time")
    if duration <= 0 or queue < 0:
        raise AllocationError("Datadog CI job duration must be positive and queue time cannot be negative")
    return JobObservation(
        pipeline_id=str(pipeline_id),
        job_id=str(job_id),
        job_name=job_name,
        stage_name=stage_name,
        suite=suite,
        strategy=strategy,
        shard_index=shard_index,
        shard_total=shard_total,
        duration_seconds=duration,
        queue_seconds=queue,
        status=str(ci.get("status", attributes.get("status", "unknown"))),
        timestamp=_timestamp(attributes),
    )


def load_job_observations(path: Path) -> list[JobObservation]:
    observations = []
    for item in load_json_documents(path):
        if item.get("schema_version") == 1 and "job_name" in item and "duration_seconds" in item:
            observations.append(JobObservation(**{key: item[key] for key in JobObservation.__dataclass_fields__}))
        else:
            observations.append(job_from_datadog(item))
    return observations


def write_job_observations(path: Path, observations: t.Iterable[JobObservation]) -> None:
    lines = [json.dumps({"schema_version": 1, **asdict(item)}, sort_keys=True) for item in observations]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
