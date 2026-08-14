#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "riot>=0.22.0",
#     "ruamel.yaml>=0.17.21",
# ]
# ///
"""Build, verify, replay, and select dd-trace-py CI shard allocations."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import typing as t


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from ci_allocation.history import build_runtime_model  # noqa: E402
from ci_allocation.history import live_shadow_report  # noqa: E402
from ci_allocation.history import load_json  # noqa: E402
from ci_allocation.history import load_observations  # noqa: E402
from ci_allocation.history import ratchet_violations  # noqa: E402
from ci_allocation.history import replay_observations  # noqa: E402
from ci_allocation.history import runtime_estimates  # noqa: E402
from ci_allocation.history import validate_runtime_model  # noqa: E402
from ci_allocation.history import write_json  # noqa: E402
from ci_allocation.history import write_observations  # noqa: E402
from ci_allocation.jobs import load_job_observations  # noqa: E402
from ci_allocation.jobs import write_job_observations  # noqa: E402
from ci_allocation.junit import verify_junit_parity  # noqa: E402
from ci_allocation.manifest import load_manifest  # noqa: E402
from ci_allocation.planner import AllocationError  # noqa: E402
from ci_allocation.planner import build_suite_plan  # noqa: E402
from ci_allocation.planner import selected_shard  # noqa: E402
from ci_allocation.pr_history import collect_pr_shapes  # noqa: E402
from ci_allocation.pr_history import load_pr_shapes  # noqa: E402
from ci_allocation.pr_history import replay_pr_shapes  # noqa: E402
from ci_allocation.pr_history import write_pr_shapes  # noqa: E402
from ci_allocation.suites import collect_all_suite_venv_info  # noqa: E402


DEFAULT_POLICY = ROOT / "ci" / "ci-allocation-policy.json"
DEFAULT_MODEL = ROOT / "ci" / "ci-allocation-runtime-model.json"
UV_REQUIRED_COMMANDS = {"export-pr-history", "replay-pr-history"}


def _suite_catalog() -> tuple[dict[str, dict[str, t.Any]], dict[str, t.Any]]:
    from tests.suitespec import get_suites

    suites = {name: dict(config) for name, config in get_suites().items() if config.get("type", "test") == "test"}
    patterns = {
        name: str(config.get("pattern", name)) for name, config in suites.items() if not config.get("skip", False)
    }
    return suites, collect_all_suite_venv_info(patterns)


def _policy(path: Path) -> dict[str, t.Any]:
    policy = load_json(path)
    if policy.get("schema_version") != 1:
        raise AllocationError("unsupported CI allocation policy schema")
    for section in ("allocation", "model", "ratchets"):
        if not isinstance(policy.get(section), dict):
            raise AllocationError(f"CI allocation policy is missing {section}")
    for ratchet in ("historical_replay", "pr_shape_replay", "live_shadow"):
        if not isinstance(policy["ratchets"].get(ratchet), dict):
            raise AllocationError(f"CI allocation policy is missing {ratchet}")
    return policy


def command_select(args: argparse.Namespace) -> None:
    hashes = [line.strip() for line in sys.stdin if line.strip()]
    model = load_json(args.model)
    strategy = args.strategy or _policy(args.policy)["allocation"]["active_strategy"]
    estimates, fallback = runtime_estimates(model)
    plan = build_suite_plan(
        suite=args.suite,
        riot_hashes=hashes,
        shard_count=args.node_total,
        estimates=estimates,
        fallback_seconds=fallback,
        execution_metadata={"suite": args.suite},
    )
    for riot_hash in selected_shard(plan, strategy, args.node_index):
        print(riot_hash)


def command_ingest(args: argparse.Namespace) -> None:
    observations = load_observations(args.input)
    write_observations(args.output, observations)
    print(f"wrote {len(observations)} observations to {args.output}")


def command_ingest_jobs(args: argparse.Namespace) -> None:
    observations = load_job_observations(args.input)
    write_job_observations(args.output, observations)
    print(f"wrote {len(observations)} CI job observations to {args.output}")


def command_build_model(args: argparse.Namespace) -> None:
    policy = _policy(args.policy)
    observations = load_observations(args.observations)
    jobs = load_job_observations(args.jobs)
    model = build_runtime_model(observations, policy["model"], jobs)
    write_json(args.output, model)
    print(
        f"wrote {len(model['estimates'])} Riot hash estimates from "
        f"{model['dataset']['training_observations']} observations to {args.output}"
    )
    if args.report is not None:
        report = replay_observations(
            observations,
            model,
            target_shard_seconds=float(policy["allocation"]["target_shard_seconds"]),
            maximum_parallelism_per_suite=int(policy["allocation"]["maximum_parallelism_per_suite"]),
        )
        write_json(args.report, report)
        print(f"wrote holdout replay to {args.report}")


def command_replay_observations(args: argparse.Namespace) -> None:
    policy = _policy(args.policy)
    report = replay_observations(
        load_observations(args.observations),
        load_json(args.model),
        target_shard_seconds=float(policy["allocation"]["target_shard_seconds"]),
        maximum_parallelism_per_suite=int(policy["allocation"]["maximum_parallelism_per_suite"]),
    )
    write_json(args.output, report)
    print(f"wrote historical replay to {args.output}")


def command_live_report(args: argparse.Namespace) -> None:
    report = live_shadow_report(load_observations(args.observations), load_job_observations(args.jobs))
    write_json(args.output, report)
    print(f"wrote live shadow replay to {args.output}")


def command_check_ratchet(args: argparse.Namespace) -> None:
    policy = _policy(args.policy)
    report = load_json(args.report)
    violations = ratchet_violations(report, policy["ratchets"])
    if violations:
        raise AllocationError("; ".join(violations))
    print(f"CI allocation ratchet passed for {report['kind']}")


def command_verify_plan(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.plan)
    print(f"CI allocation plan verified: {len(manifest['suites'])} semantic suites")


def command_verify_junit(args: argparse.Namespace) -> None:
    report = verify_junit_parity(args.legacy, args.balanced)
    if args.output is not None:
        write_json(args.output, report)
    print(
        f"JUnit allocation parity verified: {report['test_identity_count']} tests, "
        f"{report['riot_hash_count']} Riot hashes"
    )


def command_check_contract(args: argparse.Namespace) -> None:
    policy = _policy(args.policy)
    model = load_json(args.model)
    validate_runtime_model(model)
    if model["parameters"] != policy["model"]:
        raise AllocationError("runtime model parameters do not match the allocation policy")
    if int(policy["allocation"].get("target_jobs", 0)) <= 0:
        raise AllocationError("allocation target_jobs must be positive")
    if float(policy["allocation"].get("target_shard_seconds", 0)) <= 0:
        raise AllocationError("allocation target_shard_seconds must be positive")
    if int(policy["allocation"].get("maximum_parallelism_per_suite", 0)) <= 0:
        raise AllocationError("allocation maximum_parallelism_per_suite must be positive")
    maximum_model_bytes = int(policy["allocation"].get("maximum_runtime_model_bytes", 0))
    if maximum_model_bytes <= 0:
        raise AllocationError("allocation maximum_runtime_model_bytes must be positive")
    if args.model.stat().st_size > maximum_model_bytes:
        raise AllocationError(f"runtime model is {args.model.stat().st_size} bytes; maximum is {maximum_model_bytes}")
    if policy["allocation"].get("active_strategy") not in {"legacy", "balanced"}:
        raise AllocationError("allocation active_strategy must be legacy or balanced")
    if policy["allocation"]["active_strategy"] != "legacy" and not model["estimates"]:
        raise AllocationError("balanced allocation cannot be active with an empty runtime model")
    print(
        f"CI allocation contract verified: strategy={policy['allocation']['active_strategy']}, "
        f"estimates={len(model['estimates'])}"
    )


def command_export_pr_history(args: argparse.Namespace) -> None:
    from tests.suitespec import get_patterns
    from tests.suitespec import get_suites

    patterns = {
        suite: get_patterns(suite) for suite, config in get_suites().items() if config.get("type", "test") == "test"
    }
    shapes = collect_pr_shapes(
        root=ROOT,
        suite_patterns=patterns,
        since=args.since,
        max_count=args.max_count,
    )
    write_pr_shapes(args.output, shapes)
    print(f"wrote {len(shapes)} PR workload shapes to {args.output}")


def command_replay_pr_history(args: argparse.Namespace) -> None:
    policy = _policy(args.policy)
    suites, suite_info = _suite_catalog()
    report = replay_pr_shapes(
        shapes=load_pr_shapes(args.pr_history),
        suite_configs=suites,
        suite_venv_info=suite_info,
        runtime_model=load_json(args.model),
        target_jobs=int(policy["allocation"]["target_jobs"]),
        target_shard_seconds=float(policy["allocation"]["target_shard_seconds"]),
        maximum_parallelism_per_suite=int(policy["allocation"]["maximum_parallelism_per_suite"]),
    )
    write_json(args.output, report)
    print(f"wrote PR-shape replay to {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    select = subparsers.add_parser("select", help="select hashes for one CI node")
    select.add_argument("--suite", required=True)
    select.add_argument("--strategy", choices=("legacy", "balanced"))
    select.add_argument("--node-index", type=int, required=True)
    select.add_argument("--node-total", type=int, required=True)
    select.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    select.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    select.set_defaults(func=command_select)

    ingest = subparsers.add_parser("ingest-datadog", help="normalize Test Visibility session exports")
    ingest.add_argument("--input", type=Path, required=True)
    ingest.add_argument("--output", type=Path, required=True)
    ingest.set_defaults(func=command_ingest)

    ingest_jobs = subparsers.add_parser("ingest-jobs", help="normalize Datadog CI job event exports")
    ingest_jobs.add_argument("--input", type=Path, required=True)
    ingest_jobs.add_argument("--output", type=Path, required=True)
    ingest_jobs.set_defaults(func=command_ingest_jobs)

    build_model = subparsers.add_parser("build-model", help="fit a model and optionally replay its holdout")
    build_model.add_argument("--observations", type=Path, required=True)
    build_model.add_argument("--output", type=Path, required=True)
    build_model.add_argument("--jobs", type=Path, required=True)
    build_model.add_argument("--report", type=Path)
    build_model.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    build_model.set_defaults(func=command_build_model)

    replay = subparsers.add_parser("replay-observations", help="replay a model over its historical holdout")
    replay.add_argument("--observations", type=Path, required=True)
    replay.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    replay.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    replay.add_argument("--output", type=Path, required=True)
    replay.set_defaults(func=command_replay_observations)

    live_report = subparsers.add_parser("build-live-report", help="compare paired same-pipeline shadow runs")
    live_report.add_argument("--observations", type=Path, required=True)
    live_report.add_argument("--jobs", type=Path, required=True)
    live_report.add_argument("--output", type=Path, required=True)
    live_report.set_defaults(func=command_live_report)

    ratchet = subparsers.add_parser("check-ratchet", help="enforce historical or live promotion thresholds")
    ratchet.add_argument("--report", type=Path, required=True)
    ratchet.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    ratchet.set_defaults(func=command_check_ratchet)

    verify = subparsers.add_parser("verify-plan", help="verify an allocation manifest")
    verify.add_argument("--plan", type=Path, required=True)
    verify.set_defaults(func=command_verify_plan)

    verify_junit = subparsers.add_parser("verify-junit", help="compare collected tests from legacy and shadow jobs")
    verify_junit.add_argument("--legacy", type=Path, nargs="+", required=True)
    verify_junit.add_argument("--balanced", type=Path, nargs="+", required=True)
    verify_junit.add_argument("--output", type=Path)
    verify_junit.set_defaults(func=command_verify_junit)

    contract = subparsers.add_parser("check-contract", help="validate the checked-in policy and runtime model")
    contract.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    contract.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    contract.set_defaults(func=command_check_contract)

    export_prs = subparsers.add_parser("export-pr-history", help="export historical PR path-selection shapes")
    export_prs.add_argument("--since", default="2 years ago")
    export_prs.add_argument("--max-count", type=int)
    export_prs.add_argument("--output", type=Path, required=True)
    export_prs.set_defaults(func=command_export_pr_history)

    replay_prs = subparsers.add_parser("replay-pr-history", help="replay the planner over PR path-selection history")
    replay_prs.add_argument("--pr-history", type=Path, required=True)
    replay_prs.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    replay_prs.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    replay_prs.add_argument("--output", type=Path, required=True)
    replay_prs.set_defaults(func=command_replay_pr_history)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command in UV_REQUIRED_COMMANDS and os.getenv("CI_ALLOCATION_UV_REEXEC") != "1":
        environment = dict(os.environ)
        environment["CI_ALLOCATION_UV_REEXEC"] = "1"
        runner = ROOT / "scripts" / "uv-run-script"
        os.execve(str(runner), [str(runner), str(Path(__file__)), *sys.argv[1:]], environment)
    try:
        args.func(args)
    except (AllocationError, FileNotFoundError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"CI allocation error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
