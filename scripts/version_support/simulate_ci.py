"""Simulate the version-support child pipeline locally.

This is intentionally scoped to the VERSION_SUPPORT_SPEC_JSON flow: provide a
specific integration/package/Python-version spec, generate the same child YAML
as CI, inspect which test jobs and riot hashes CI would run, and optionally run
the prepare/build/test flow locally.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import typing as t

from version_support.ci_specs import load_integration_names_from_json_text
from version_support.ci_specs import resolve_suite_names_for_integrations
from version_support.gen_child_pipeline import ROOT
from version_support.gen_child_pipeline import _calculate_parallelism_from_venvs
from version_support.gen_child_pipeline import _collect_suite_venv_info
from version_support.gen_child_pipeline import _get_suites
from version_support.gen_child_pipeline import _load_riot_venv
from version_support.gen_child_pipeline import _pip_cache_key
from version_support.gen_child_pipeline import _rewrite_riotfile
from version_support.gen_child_pipeline import generate as generate_child_pipeline
from version_support.run_specific_integrations_versions import run as run_specific_integrations_versions


RIOT_RUN_CMD = ["riot", "-P", "-v", "run", "--exitfirst", "--pass-env", "-s"]
CI_UNSET_ENV = (
    "DD_SERVICE",
    "DD_ENV",
    "DD_TAGS",
    "DD_TRACE_REMOVE_INTEGRATION_SERVICE_NAMES_ENABLED",
)
BUILD_ENV = {
    "CMAKE_BUILD_PARALLEL_LEVEL": "12",
    "PIP_VERBOSE": "0",
    "DD_PROFILING_NATIVE_TESTS": "1",
    "DD_USE_SCCACHE": "1",
    "DD_PROFILING_MEMALLOC_ASSERT_ON_REENTRY": "1",
    "CARGO_PROFILE_RELEASE_LTO": "off",
    "CARGO_PROFILE_RELEASE_CODEGEN_UNITS": "16",
    "CARGO_PROFILE_RELEASE_OPT_LEVEL": "2",
}


def _local_testagent_url(env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    testagent_port = source.get("TESTAGENT_HOST_PORT", "9126")
    return f"http://127.0.0.1:{testagent_port}"


@dataclass(frozen=True)
class _SuitePlan:
    suite_name: str
    clean_name: str
    pattern: str
    env: dict[str, str]
    services: list[str]
    snapshot: bool
    py_versions: list[str]
    parallelism: int
    suite_env_name: str
    pip_cache_key: str
    hashes: list[str]
    shards: list[list[str]]


@contextmanager
def _temporary_env(**updates: str) -> t.Iterator[None]:
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _prepared_riotfile(rewritten_source: str, *, keep: bool) -> t.Iterator[None]:
    riotfile_path = ROOT / "riotfile.py"
    requirements_path = ROOT / ".riot" / "requirements"
    original_source = riotfile_path.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="simulate-ci-requirements-") as temp_dir:
        requirements_backup_path = Path(temp_dir) / "requirements"
        requirements_existed = requirements_path.exists()
        if requirements_existed:
            shutil.copytree(requirements_path, requirements_backup_path)

        riotfile_path.write_text(rewritten_source, encoding="utf-8")
        try:
            yield
        finally:
            if not keep:
                riotfile_path.write_text(original_source, encoding="utf-8")
                if requirements_path.exists():
                    shutil.rmtree(requirements_path)
                if requirements_existed:
                    shutil.copytree(requirements_backup_path, requirements_path)


def _load_spec_json(args: argparse.Namespace) -> str:
    if args.spec_json:
        return json.dumps(json.loads(args.spec_json))

    env_spec_json = os.environ.get("VERSION_SUPPORT_SPEC_JSON")
    if env_spec_json:
        return json.dumps(json.loads(env_spec_json))

    spec_file = args.spec_file
    if spec_file is not None:
        return json.dumps(json.loads(spec_file.read_text(encoding="utf-8")))

    raise RuntimeError("Provide --spec-json, VERSION_SUPPORT_SPEC_JSON, or --spec-file")


def _split_hashes(hashes: list[str], node_total: int) -> list[list[str]]:
    shards: list[list[str]] = [[] for _ in range(node_total)]
    for index, value in enumerate(hashes):
        shards[index % node_total].append(value)
    return shards


def _riot_hashes(suite_pattern: str) -> list[str]:
    output = subprocess.check_output(
        ["riot", "list", "--hash-only", suite_pattern],
        cwd=ROOT,
        text=True,
    )
    return sorted(line.strip() for line in output.splitlines() if line.strip())


def _run_command(args: list[str], *, env: dict[str, str] | None = None) -> None:
    print(f"+ {' '.join(args)}")
    subprocess.run(args, cwd=ROOT, env=env, check=True)


def _run_bash(command: str, *, env: dict[str, str] | None = None) -> None:
    print(f"+ {command}")
    subprocess.run(["bash", "-lc", command], cwd=ROOT, env=env, check=True)


def _run_ddtest_command(args: list[str], *, env: dict[str, str] | None = None) -> None:
    _run_command(["scripts/ddtest", *args], env=env)


def _ddtest_env_command(command: list[str], env: dict[str, str]) -> list[str]:
    forwarded = [
        f"{key}={value}"
        for key, value in sorted(env.items())
        if key in BUILD_ENV or key in {"PIP_PRE", "PYTHON_VERSION"}
    ]
    if not forwarded:
        return command
    return ["env", *forwarded, *command]


def _freeze_generated_venv(py_version: str, *, env: dict[str, str]) -> None:
    py_tag = py_version.replace(".", "")
    script = """
from pathlib import Path
import subprocess
import sys

py_tag, py_version = sys.argv[1:3]
pip_paths = sorted(
    Path(".riot").glob(f"venv_py{py_tag}*/bin/pip"),
    key=lambda path: path.stat().st_mtime,
    reverse=True,
)
for pip_path in pip_paths:
    probe = subprocess.run(
        [str(pip_path), "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode == 0:
        raise SystemExit(subprocess.run([str(pip_path), "freeze"], check=False).returncode)

if pip_paths:
    print(f"Found {len(pip_paths)} pip executable(s) for Python {py_version}, but none could run.", file=sys.stderr)
else:
    print(f"No generated riot venv pip executable found for Python {py_version}.", file=sys.stderr)
raise SystemExit(1)
""".strip()
    _run_ddtest_command(_ddtest_env_command(["python", "-c", script, py_tag, py_version], env), env=env)


def _ci_like_env(*, snapshot: bool = False, suite_env: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["CI_PROJECT_DIR"] = str(ROOT)
    env["PIP_CACHE_DIR"] = str(ROOT / ".cache" / "pip")
    env.setdefault("NIGHTLY_BUILD", "false")
    for key in CI_UNSET_ENV:
        env.pop(key, None)
    if snapshot:
        env["DD_TRACE_AGENT_URL"] = _local_testagent_url(env)
    if suite_env is not None:
        env.update(suite_env)
    return env


def _run_ci_hash(hash_value: str, *, env: dict[str, str]) -> None:
    _run_command(["riot", "list", hash_value], env=env)
    run_env = env.copy()
    run_env["_CI_DD_TAGS"] = f"test.configuration.riot_hash:{hash_value}"
    _run_ddtest_command([*RIOT_RUN_CMD, hash_value, "--", "--ddtrace"], env=run_env)


def _job_parallelism(
    *,
    suite_name: str,
    suite_config: dict[str, t.Any],
    suite_venv_info: dict[str, tuple[int, set[str]]],
) -> int:
    static_parallelism = suite_config.get("parallelism")
    if static_parallelism is not None:
        return int(static_parallelism)

    venvs_per_job = suite_config.get("venvs_per_job")
    if venvs_per_job is not None and suite_name in suite_venv_info:
        venv_count, _ = suite_venv_info[suite_name]
        return _calculate_parallelism_from_venvs(venv_count, int(venvs_per_job))

    return 1


def _start_services(services: set[str]) -> None:
    if not services:
        return
    _run_command(["docker", "compose", "up", "-d", *sorted(services)])


def _run_prepare_step() -> None:
    # Local runs should use ddtest so requirement compilation happens in the
    # project's managed environment instead of the system Python.
    _run_command(["scripts/ddtest", "scripts/compile-and-prune-test-requirements"])


def _run_build_step(py_versions: list[str]) -> None:
    if not py_versions:
        py_versions = ["3.12"]

    unpin = os.getenv("UNPIN_DEPENDENCIES", "false") or "false"
    nightly = os.getenv("NIGHTLY_BUILD", "false") or "false"
    print("Building base venvs:")
    print(f"  UNPIN_DEPENDENCIES: {unpin}")
    print(f"  NIGHTLY_BUILD: {nightly}")

    env = _ci_like_env()
    env.update(BUILD_ENV)
    if unpin == "true":
        _run_ddtest_command(_ddtest_env_command(["python", "scripts/allow_prerelease_dependencies.py"], env), env=env)
        env["PIP_PRE"] = "true"

    for py_version in py_versions:
        py_env = env.copy()
        py_env["PYTHON_VERSION"] = py_version
        _run_ddtest_command(
            _ddtest_env_command(["riot", "-P", "-v", "generate", f"--python={py_version}"], py_env), env=py_env
        )
        _freeze_generated_venv(py_version, env=py_env)
        print("Running smoke tests")
        _run_ddtest_command(
            _ddtest_env_command(["riot", "-v", "run", "-s", f"--python={py_version}", "smoke_test"], py_env),
            env=py_env,
        )


def _run_test_step(plans: list[_SuitePlan], *, start_services: bool) -> None:
    services_to_start: set[str] = set()
    wait_for: set[str] = set()
    for plan in plans:
        services_to_start.update(plan.services)
        wait_for.update(plan.services)
        if plan.snapshot:
            services_to_start.add("testagent")
            wait_for.add("testagent")

    if start_services:
        _start_services(services_to_start)
    elif wait_for:
        print("Skipping docker compose service startup; assuming required services are already running.")

    if wait_for:
        wait_env = _ci_like_env(snapshot="testagent" in wait_for)
        if "testagent" in wait_for:
            wait_env["DD_TESTAGENT_URL"] = _local_testagent_url(wait_env)
        _run_ddtest_command(["riot", "-v", "run", "-s", "--pass-env", "wait", "--", *sorted(wait_for)], env=wait_env)

    for plan in plans:
        suite_env = {
            "SUITE_NAME": plan.suite_env_name,
            "PIP_CACHE_DIR": str(ROOT / ".cache" / "pip"),
            "PIP_CACHE_KEY": plan.pip_cache_key,
        }
        suite_env.update(plan.env)
        env = _ci_like_env(snapshot=plan.snapshot, suite_env=suite_env)
        for hash_value in plan.hashes:
            _run_ci_hash(hash_value, env=env)


def _run_local_ci_flow(
    plans: list[_SuitePlan],
    *,
    py_versions: list[str],
    start_services: bool,
    skip_build: bool,
) -> None:
    print("Running local CI flow")
    print("Prepare: compiling and pruning riot requirement lockfiles")
    _run_prepare_step()
    if skip_build:
        print("Build: skipped by --skip-build")
    else:
        _run_build_step(py_versions)
    print("Test: running selected riot hashes")
    _run_test_step(plans, start_services=start_services)


def _rewrite_riotfile_only(spec_json: str) -> None:
    print("Stopping after riotfile rewrite")
    run_specific_integrations_versions(["--spec-json", spec_json])


def _describe_ci_flow(
    spec_json: str,
    *,
    pipeline_output: Path,
    run_tests: bool,
    keep_riotfile: bool,
    start_services: bool,
    skip_build: bool,
) -> None:
    spec_data = json.loads(spec_json)
    all_suites = _get_suites()
    integration_names = load_integration_names_from_json_text(spec_json)
    suite_names = resolve_suite_names_for_integrations(integration_names, suites=all_suites)

    rewritten_source = _rewrite_riotfile(spec_data)
    riot_venv = _load_riot_venv(rewritten_source)

    suite_patterns: dict[str, str] = {}
    for suite_name in suite_names:
        suite_config = all_suites.get(suite_name, {})
        suite_patterns[suite_name] = suite_config.get("pattern", suite_name.split("::")[-1])

    suite_venv_info = _collect_suite_venv_info(suite_patterns, riot_venv)

    print(f"Generated child pipeline: {pipeline_output}")
    print("CI prepare job:")
    print("  PYTHONPATH=scripts python -m version_support.run_specific_integrations_versions \\")
    print('    --spec-json "$VERSION_SUPPORT_SPEC_JSON"')
    print("  scripts/compile-and-prune-test-requirements")
    print("")

    with _prepared_riotfile(rewritten_source, keep=keep_riotfile):
        if keep_riotfile:
            print("Applied version-support rewrite to riotfile.py")
        else:
            print("Temporarily applied version-support rewrite to inspect riot hashes")
        print("")

        plans: list[_SuitePlan] = []
        for suite_name in suite_names:
            suite_config = all_suites.get(suite_name, {})
            if suite_config.get("skip", False):
                print(f"Skipping {suite_name}: marked skip in suitespec.yml")
                continue

            clean_name = suite_name.split("::")[-1]
            pattern = suite_patterns[suite_name]
            env = dict(suite_config.get("env") or {})
            services: list[str] = suite_config.get("services") or []
            snapshot = bool(suite_config.get("snapshot", False))
            if snapshot:
                services = [*services, "testagent"]

            py_versions = sorted(suite_venv_info.get(suite_name, (0, set()))[1])
            parallelism = _job_parallelism(
                suite_name=suite_name,
                suite_config=suite_config,
                suite_venv_info=suite_venv_info,
            )
            suite_env_name = env.get("SUITE_NAME", pattern)
            hashes = _riot_hashes(suite_env_name)
            shards = _split_hashes(hashes, parallelism)
            plans.append(
                _SuitePlan(
                    suite_name=suite_name,
                    clean_name=clean_name,
                    pattern=pattern,
                    env=env,
                    services=services,
                    snapshot=snapshot,
                    py_versions=py_versions,
                    parallelism=parallelism,
                    suite_env_name=suite_env_name,
                    pip_cache_key=_pip_cache_key(suite_env_name),
                    hashes=hashes,
                    shards=shards,
                )
            )

            print(f"test/{clean_name}:")
            print(f"  SUITE_NAME: {suite_env_name}")
            print(f"  pattern: {pattern}")
            print(f"  python versions: {', '.join(py_versions) if py_versions else '(none found)'}")
            print(f"  parallel: {parallelism}")
            print(f"  pip cache key: {plans[-1].pip_cache_key}")
            if services:
                print(f"  waits for services: {', '.join(sorted(set(services)))}")
            print(f"  hashes ({len(hashes)}): {', '.join(hashes) if hashes else '(none)'}")
            if parallelism > 1:
                for index, shard in enumerate(shards, start=1):
                    print(f"    node {index}/{parallelism}: {', '.join(shard) if shard else '(none)'}")
            print("  CI run commands:")
            for hash_value in hashes:
                print(f"    riot list {hash_value}")
                print(f"    {' '.join(RIOT_RUN_CMD)} {hash_value} -- --ddtrace")
            print("")

        if run_tests:
            all_py_versions = sorted({py_version for plan in plans for py_version in plan.py_versions})
            _run_local_ci_flow(
                plans,
                py_versions=all_py_versions,
                start_services=start_services,
                skip_build=skip_build,
            )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec-json",
        help="Raw VERSION_SUPPORT_SPEC_JSON value. Defaults to the VERSION_SUPPORT_SPEC_JSON environment variable.",
    )
    parser.add_argument(
        "--spec-file",
        type=Path,
        help="Read VERSION_SUPPORT_SPEC_JSON from a file when --spec-json and the environment variable are absent.",
    )
    parser.add_argument(
        "--pipeline-output",
        type=Path,
        default=ROOT / "version-support-pipeline.yml",
        help="Where to write the generated child pipeline YAML.",
    )
    parser.add_argument(
        "--keep-riotfile",
        action="store_true",
        help="Leave riotfile.py and .riot/requirements rewritten after the simulation. By default both are restored.",
    )
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help=(
            "Run the local CI flow after printing it: compile requirements, build venvs, "
            "wait for services, then run tests."
        ),
    )
    parser.add_argument(
        "--no-start-services",
        action="store_true",
        help="Do not run `docker compose up -d` for services needed by selected suites.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="With --run-tests, skip the build_base_venvs step and reuse existing local riot venvs.",
    )
    parser.add_argument(
        "--stop-after-rewrite",
        action="store_true",
        help="Apply version-support rewrite to riotfile.py and stop before requirements/hash/build/test steps.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if args.stop_after_rewrite and args.run_tests:
        raise RuntimeError("--stop-after-rewrite cannot be combined with --run-tests")

    spec_json = _load_spec_json(args)

    with _temporary_env(VERSION_SUPPORT_SPEC_JSON=spec_json):
        generate_child_pipeline(args.pipeline_output)

    if args.stop_after_rewrite:
        _rewrite_riotfile_only(spec_json)
        return 0

    _describe_ci_flow(
        spec_json,
        pipeline_output=args.pipeline_output,
        run_tests=args.run_tests,
        keep_riotfile=args.keep_riotfile,
        start_services=not args.no_start_services,
        skip_build=args.skip_build,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as e:
        raise SystemExit(e.returncode)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1)
