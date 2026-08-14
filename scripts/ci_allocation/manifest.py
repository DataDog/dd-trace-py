"""Create and validate the generated legacy-versus-balanced plan artifact."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import typing as t

from .history import runtime_estimates
from .history import validate_runtime_model
from .planner import AllocationError
from .planner import build_suite_plan
from .planner import verify_assignments
from .planner import verify_runtime_assignments
from .suites import SuiteVenvInfo
from .suites import runtime_setup_seconds
from .suites import runtime_test_item_counts


def _json_value(value: t.Any) -> t.Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_json_value(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise AllocationError(f"execution metadata contains an unsupported value: {type(value).__name__}")


def _digest(value: t.Mapping[str, t.Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_allocation_manifest(
    *,
    suite_venv_info: t.Mapping[str, SuiteVenvInfo],
    suite_configs: t.Mapping[str, t.Mapping[str, t.Any]],
    legacy_shard_counts: t.Mapping[str, int],
    balanced_shard_counts: t.Mapping[str, int],
    runtime_model: t.Mapping[str, t.Any],
    active_strategy: str,
    target_shard_seconds: float = 300.0,
    maximum_slices_per_hash: int = 1,
) -> dict[str, t.Any]:
    validate_runtime_model(runtime_model)
    _estimates, global_fallback = runtime_estimates(runtime_model)
    suite_fallbacks = runtime_model["fallbacks"].get("suite_seconds", {})

    plans = []
    if active_strategy not in {"legacy", "balanced"}:
        raise AllocationError("allocation manifest active strategy is invalid")
    if set(legacy_shard_counts) != set(balanced_shard_counts):
        raise AllocationError("legacy and balanced plans must contain identical semantic suites")

    for suite in sorted(legacy_shard_counts):
        info = suite_venv_info.get(suite)
        if info is None:
            raise AllocationError(f"selected suite has no Riot hashes: {suite}")
        config = _json_value(suite_configs[suite])
        fallback = float(suite_fallbacks.get(suite, global_fallback))
        estimates, _unused_fallback = runtime_estimates(runtime_model, suite)
        plans.append(
            build_suite_plan(
                suite=suite,
                riot_hashes=info.hashes,
                shard_count=legacy_shard_counts[suite],
                balanced_shard_count=balanced_shard_counts[suite],
                estimates=estimates,
                fallback_seconds=fallback,
                execution_metadata=config,
                overhead_seconds=runtime_setup_seconds(runtime_model, suite),
                target_shard_seconds=target_shard_seconds,
                test_item_counts=runtime_test_item_counts(info, runtime_model),
                maximum_slices_per_hash=maximum_slices_per_hash,
            )
        )

    manifest: dict[str, t.Any] = {
        "schema_version": 1,
        "planner_version": "runtime-sliced-lpt-v2",
        "active_strategy": active_strategy,
        "runtime_model_sha256": _digest(runtime_model),
        "suites": plans,
    }
    manifest["manifest_sha256"] = _digest(manifest)
    verify_allocation_manifest(manifest)
    return manifest


def verify_allocation_manifest(manifest: t.Mapping[str, t.Any]) -> None:
    if manifest.get("schema_version") != 1 or manifest.get("planner_version") != "runtime-sliced-lpt-v2":
        raise AllocationError("unsupported allocation manifest schema or planner version")
    if manifest.get("active_strategy") not in {"legacy", "balanced"}:
        raise AllocationError("allocation manifest active strategy is invalid")
    suites = manifest.get("suites")
    if not isinstance(suites, list):
        raise AllocationError("allocation manifest suites must be a list")
    suite_names = []
    for raw_plan in suites:
        if not isinstance(raw_plan, dict):
            raise AllocationError("allocation manifest suite plan must be an object")
        suite = raw_plan.get("suite")
        hashes = raw_plan.get("riot_hashes")
        if (
            not isinstance(suite, str)
            or not isinstance(hashes, list)
            or not all(isinstance(item, str) for item in hashes)
        ):
            raise AllocationError("allocation manifest suite identity is malformed")
        suite_names.append(suite)
        for strategy in ("legacy", "balanced"):
            strategy_plan = raw_plan.get(strategy)
            if not isinstance(strategy_plan, dict) or not isinstance(strategy_plan.get("assignments"), list):
                raise AllocationError(f"allocation manifest {suite} {strategy} plan is malformed")
            assignments = strategy_plan["assignments"]
            if not all(
                isinstance(shard, list) and all(isinstance(item, str) for item in shard) for shard in assignments
            ):
                raise AllocationError(f"allocation manifest {suite} {strategy} assignments are malformed")
            if strategy == "legacy":
                verify_assignments(hashes, assignments)
            else:
                verify_runtime_assignments(hashes, assignments)
        parity = raw_plan.get("parity")
        expected_parity = {
            "exact_union",
            "no_overlap",
            "no_empty_shards",
            "execution_metadata_equal",
            "complete_runtime_slices",
        }
        if not isinstance(parity, dict) or set(parity) != expected_parity or not all(parity.values()):
            raise AllocationError(f"allocation manifest parity proof failed for {suite}")
    if len(suite_names) != len(set(suite_names)):
        raise AllocationError("allocation manifest contains duplicate suites")

    expected_digest = manifest.get("manifest_sha256")
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256", None)
    if expected_digest != _digest(unsigned):
        raise AllocationError("allocation manifest digest does not match its content")


def load_manifest(path: Path) -> dict[str, t.Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AllocationError("allocation manifest must contain a JSON object")
    verify_allocation_manifest(value)
    return value


def write_manifest(path: Path, manifest: t.Mapping[str, t.Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
