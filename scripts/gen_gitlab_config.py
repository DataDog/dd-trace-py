#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "riot>=0.22.0",
#     "ruamel.yaml>=0.17.21",
#     "lxml>=4.9.0",
# ]
# ///
"""
This script is used to generate the GitLab dynamic config file in
.gitlab/tests.yml.

To add new configuration manipulations that are based on top of the template
file in .gitlab/tests.yml, add a function named gen_<name> to this
file. The function will be called automatically when this script is run.
"""

from collections import defaultdict
from dataclasses import dataclass
import datetime
import hashlib
import os
import re
import subprocess
import typing as t


MAX_BENCHMARKS_PER_GROUP = 2
BENCHMARK_CLASS_REGEX = r"class ([A-Za-z]+)\((bm\.)?Scenario(.+)?\)\:"
BENCHMARK_SCENARIO_REGEX = re.compile(" +- name: ([a-z0-9]+)-.+")


def _get_bool_env(name: str) -> str:
    """Return "true"/"false" for a boolean environment variable.

    The result is interpolated verbatim into generated shell snippets, so it must never carry
    anything the shell could expand. Only a case-insensitive "true" enables the flag; anything else
    is treated as false.
    """
    value = os.getenv(name, "").lower()
    if value not in ("", "true", "false"):
        LOGGER.warning("Ignoring unexpected value for %s, treating it as false", name)
    return "true" if value == "true" else "false"


@dataclass
class BenchmarkSpec:
    name: str
    cpus_per_run: t.Optional[int] = 1
    pattern: t.Optional[str] = None
    paths: t.Optional[set[str]] = None  # ignored
    skip: bool = False
    type: str = "benchmark"  # ignored


@dataclass
class JobSpec:
    name: str
    stage: str
    pattern: t.Optional[str] = None
    snapshot: bool = False
    services: t.Optional[list[str]] = None
    env: t.Optional[dict[str, str]] = None
    parallelism: t.Optional[int] = None
    venvs_per_job: t.Optional[int] = None
    retry: t.Optional[int] = None
    timeout: t.Optional[int] = None
    skip: bool = False
    allow_failure: bool = False
    paths: t.Optional[set[str]] = None  # ignored
    only: t.Optional[set[str]] = None  # ignored
    gpu: bool = False
    type: str = "test"  # ignored
    skip_pip_cache: bool = False

    python_versions: t.Optional[set[str]] = None

    def __str__(self) -> str:
        lines = []
        base = ".test_base_riot"
        if self.gpu:
            base += "_gpu"
        if self.snapshot:
            base += "_snapshot"

        lines.append(f"{self.stage}/{self.name.replace('::', '/')}:")
        lines.append(f"  extends: {base}")

        # Set stage
        lines.append(f"  stage: {self.stage}")

        # Jobs need build_base_venvs artifacts
        lines.append("  needs:")
        lines.append("    - prechecks")
        if self.python_versions:
            lines.append("    - job: build_base_venvs")
            lines.append("      artifacts: true")
            lines.append("      parallel:")
            lines.append("        matrix:")
            for pv in sorted(self.python_versions):
                lines.append(f'          - PYTHON_VERSION: "{pv}"')
        else:
            lines.append("    - job: build_base_venvs")
            lines.append("      artifacts: true")

        # Preserve declared order (dedup via dict.fromkeys) rather than using a set:
        # some services depend on others being ready first (e.g. azureeventhubsemulator
        # depends on azurite), and the wait script checks readiness in argument order.
        services = list(dict.fromkeys(self.services or []))
        if services:
            lines.append("  services:")

            _services = [f"!reference [.services, {_}]" for _ in services]
            if self.snapshot:
                _services.insert(0, f"!reference [{base}, services]")

            for service in _services:
                lines.append(f"    - {service}")

        wait_for = list(services)
        if self.snapshot:
            wait_for.append("testagent")

        # Bake NIGHTLY_BUILD into script (same approach as build_base_venvs template)
        # so the value is set when tests-gen runs and is present in the child job.
        _nightly_build = _get_bool_env("NIGHTLY_BUILD")
        lines.append("  before_script:")
        lines.append(f"    - !reference [{base}, before_script]")
        lines.append("    - pip cache info")
        lines.append(f'    - export NIGHTLY_BUILD="{_nightly_build}"')
        if wait_for:
            lines.append(f"    - riot -v run -s --pass-env wait -- {' '.join(wait_for)}")

        env = dict(self.env or {})
        if not env or "SUITE_NAME" not in env:
            env["SUITE_NAME"] = self.pattern or self.name

        suite_name = env["SUITE_NAME"]
        env["PIP_CACHE_DIR"] = "${CI_PROJECT_DIR}/.cache/pip"
        env["PIP_CACHE_KEY"] = (
            subprocess.check_output([".gitlab/scripts/get-riot-pip-cache-key.sh", suite_name]).decode().strip()
        )
        if not self.skip_pip_cache:
            lines.append("  cache:")
            lines.append(f"    key: v1-pip-${'{PIP_CACHE_KEY}'}-{TESTRUNNER_IMAGE_HASH}-cache")
            lines.append("    paths:")
            lines.append("      - .cache")

        lines.append("  variables:")
        for key, value in env.items():
            lines.append(f"    {key}: {value}")

        if self.only:
            lines.append("  only:")
            for value in self.only:
                lines.append(f"    - {value}")

        if self.parallelism is not None:
            lines.append(f"  parallel: {self.parallelism}")

        if self.retry is not None:
            lines.append(f"  retry: {self.retry}")

        if self.timeout is not None:
            lines.append(f"  timeout: {self.timeout}")

        if self.allow_failure:
            lines.append("  allow_failure: true")

        return "\n".join(lines)


# Module-level state: populated by gen_required_suites, consumed by gen_build_base_venvs
_global_python_versions: set[str] = set()

# All supported Python versions (fallback when no venv info is available)
ALL_PYTHON_VERSIONS = ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]


def gen_required_suites() -> None:
    """Generate the list of test and benchmark suites that need to be run."""
    import suitespec

    suites = suitespec.get_suites()

    required_suites: list[str] = []

    if args.suites:
        # --suite: explicit suite selection, bypass PR/file detection entirely
        unknown = [s for s in args.suites if s not in suites]
        if unknown:
            LOGGER.warning("Unknown suite(s) specified via --suite: %s", unknown)
        required_suites = [s for s in args.suites if s in suites]
        LOGGER.info("Using explicit suite selection: %s", required_suites)
    elif args.files:
        # --file: match supplied files against suite patterns (same logic as needs_testrun
        # but without any GitHub API calls)
        import fnmatch

        changed_files = set(args.files)
        for suite in sorted(suites.keys()):
            patterns = suitespec.get_patterns(suite)
            if not patterns:
                required_suites.append(suite)
                continue
            if any(fnmatch.filter(changed_files, p) for p in patterns):
                required_suites.append(suite)
        LOGGER.info("File-based suite selection found %d suite(s)", len(required_suites))
    else:
        from needs_testrun import extract_git_commit_selections
        from needs_testrun import for_each_testrun_needed

        for_each_testrun_needed(
            suites=sorted(suites.keys()),
            action=lambda suite: required_suites.append(suite),
            git_selections=extract_git_commit_selections(os.getenv("CI_COMMIT_MESSAGE", "")),
        )

    # If the ci_visibility suite is in the list of required suites, we need to run all suites
    ci_visibility_suites = {"ci_visibility", "pytest"}
    if any(suite in required_suites for suite in ci_visibility_suites):
        required_suites = sorted(suites.keys())

    _gen_tests(suites, required_suites)
    _gen_benchmarks(suites, required_suites)


def _gen_benchmarks(suites: dict, required_suites: list[str]) -> None:
    suites = {k: v for k, v in suites.items() if "benchmark" in v.get("type", "test")}
    required_suites = [a for a in required_suites if a in list(suites.keys())]

    if not required_suites:
        MICROBENCHMARKS_GEN.write_text(
            """
microbenchmark-noop:
  tags: [ "arch:amd64" ]
  script: |
    echo "noop"
"""
        )
        return

    MICROBENCHMARKS_GEN.write_text((GITLAB / "benchmarks/microbenchmarks.yml").read_text())

    for suite_name, suite_config in suites.items():
        clean_name = suite_name.split("::")[-1]
        suite_config["_clean_name"] = clean_name

    groups = defaultdict(list)

    benchmark_classnames = []

    for suite in required_suites:
        suite_config = suites[suite].copy()
        clean_name = suite_config.pop("_clean_name", suite)
        benchmark_classnames.append(_get_benchmark_class_name(clean_name))

        jobspec = BenchmarkSpec(clean_name, **suite_config)
        if jobspec.skip:
            LOGGER.debug("Skipping suite %s", suite)
            continue

        groups[jobspec.cpus_per_run].append(jobspec)

    with MICROBENCHMARKS_GEN.open("a") as f:
        for cpus_per_run, jobspecs in groups.items():
            print(f'      - CPUS_PER_RUN: "{cpus_per_run}"\n        SCENARIOS:', file=f)
            jobspecs = sorted(jobspecs, key=lambda s: s.name)
            # DEV: The organization into these groups is mostly arbitrary, based on observed runtimes and
            #      trying to keep total runtime per job <10 minutes
            for subgroup in [
                jobspecs[i : i + MAX_BENCHMARKS_PER_GROUP] for i in range(0, len(jobspecs), MAX_BENCHMARKS_PER_GROUP)
            ]:
                names = [i.name for i in subgroup]
                group_spec = f'        - "{" ".join(names)}"'
                print(group_spec, file=f)

    _filter_benchmarks_slos_file(benchmark_classnames)


def _get_benchmark_class_name(suite_name: str) -> str:
    contents = Path(f"benchmarks/{suite_name}/scenario.py").read_text()
    for line in contents.split("\n"):
        match = re.match(BENCHMARK_CLASS_REGEX, line)
        if match:
            return match.group(1).lower()


def _filter_benchmarks_slos_file(classnames: list) -> None:
    in_scenario_to_keep = True
    new_contents = []
    kept_scenarios = 0
    contents = MICROBENCHMARKS_SLOS_TEMPLATE.read_text()

    for line in contents.split("\n")[1:]:
        match = re.match(BENCHMARK_SCENARIO_REGEX, line)
        if match:
            class_on_line = match.group(1)
            if class_on_line in classnames:
                in_scenario_to_keep = True
                kept_scenarios += 1
            else:
                in_scenario_to_keep = False
        if line.strip().startswith("#"):
            in_scenario_to_keep = False
        if in_scenario_to_keep:
            new_contents.append(line)

    if kept_scenarios == 0:
        new_contents[-1] = "scenarios: []"

    MICROBENCHMARKS_SLOS.write_text("\n".join(new_contents))


def _gen_tests(suites: dict, required_suites: list[str]) -> None:
    global _global_python_versions

    suites = {k: v for k, v in suites.items() if v.get("type", "test") == "test"}
    required_suites = [a for a in required_suites if a in list(suites.keys())]

    # Copy the template file
    TESTS_GEN.write_text((GITLAB / "tests.yml").read_text())

    # Collect stages from suite configurations
    stages = {"setup"}  # setup is always needed
    for suite_name, suite_config in suites.items():
        # Extract stage from suite name prefix if present
        suite_parts = suite_name.split("::")[-2:]
        if len(suite_parts) == 2:
            stage, clean_name = suite_parts
        else:
            stage = "core"
            clean_name = suite_parts[-1]

        stages.add(stage)
        # Store the stage in the suite config for later use
        suite_config["_stage"] = stage
        suite_config["_clean_name"] = clean_name

    # Sort stages: setup first, then alphabetically
    sorted_stages = ["setup"] + sorted(stages - {"setup"})

    # Update the stages in the generated file
    content = TESTS_GEN.read_text()

    stages_yaml = "stages:\n" + "\n".join(f"  - {stage}" for stage in sorted_stages)
    content = re.sub(r"stages:.*?(?=\n\w|\n\n|\Z)", stages_yaml, content, flags=re.DOTALL)
    TESTS_GEN.write_text(content)

    # === PASS 1: Collect venv info for all non-skipped required suites ===
    non_skipped = [s for s in required_suites if not suites[s].get("skip", False)]
    suite_patterns = {s: suites[s].get("pattern", s) for s in non_skipped}
    suite_venv_info = collect_all_suite_venv_info(suite_patterns)
    missing_suites = sorted(set(non_skipped) - set(suite_venv_info))
    if missing_suites:
        raise ValueError(f"selected suites have no Riot environments: {missing_suites}")

    # Populate the module-level global so gen_build_base_venvs can use it
    _global_python_versions = set()
    for info in suite_venv_info.values():
        _global_python_versions.update(info.python_versions)

    allocation_policy = load_json(CI_ALLOCATION_POLICY)
    allocation_config = allocation_policy["allocation"]
    legacy_jobs = compute_parallelism(
        suites,
        non_skipped,
        suite_venv_info,
        target_jobs=int(allocation_config["target_jobs"]),
    )

    runtime_model = load_json(CI_ALLOCATION_MODEL)
    estimates, global_fallback = runtime_estimates(runtime_model)
    balanced_jobs = (
        compute_runtime_parallelism(
            suite_venv_info,
            non_skipped,
            estimates,
            runtime_model["fallbacks"].get("suite_seconds", {}),
            global_fallback,
            target_shard_seconds=float(allocation_config["target_shard_seconds"]),
            maximum_parallelism_per_suite=int(allocation_config["maximum_parallelism_per_suite"]),
            suite_overheads=runtime_model["overheads"].get("suite_seconds", {}),
            global_overhead=float(runtime_model["overheads"]["global_seconds"]),
        )
        if estimates
        else legacy_jobs
    )
    active_strategy = allocation_config["active_strategy"]
    if active_strategy not in {"legacy", "balanced"}:
        raise ValueError(f"unsupported CI allocation strategy: {active_strategy}")
    if active_strategy == "balanced" and not estimates:
        raise ValueError("balanced CI allocation requires a populated runtime model")
    final_jobs = legacy_jobs if active_strategy == "legacy" else balanced_jobs
    LOGGER.info("Selected %s suite jobs: %d", active_strategy, sum(final_jobs.values()))
    shadow_enabled = _get_bool_env("CI_ALLOCATION_SHADOW") == "true"
    if shadow_enabled and (active_strategy != "legacy" or not estimates):
        raise ValueError("allocation shadowing requires a populated model with the legacy strategy active")
    plan_configs = {
        suite: {key: value for key, value in suites[suite].items() if not key.startswith("_")} for suite in non_skipped
    }
    allocation_manifest = build_allocation_manifest(
        suite_venv_info=suite_venv_info,
        suite_configs=plan_configs,
        legacy_shard_counts=legacy_jobs,
        balanced_shard_counts=balanced_jobs,
        runtime_model=runtime_model,
        active_strategy=active_strategy,
    )
    write_manifest(CI_ALLOCATION_PLAN, allocation_manifest)

    # === PASS 2: Emit YAML ===
    with TESTS_GEN.open("a") as f:
        for suite in required_suites:
            suite_config = suites[suite].copy()
            stage = suite_config.pop("_stage", "core")
            clean_name = suite_config.pop("_clean_name", suite)
            suite_config["env"] = dict(suite_config.get("env") or {})
            suite_config["env"]["CI_ALLOCATION_STRATEGY"] = active_strategy

            py_versions = set(suite_venv_info[suite].python_versions) if suite in suite_venv_info else None
            jobspec = JobSpec(clean_name, stage=stage, python_versions=py_versions, **suite_config)
            if jobspec.skip:
                LOGGER.debug("Skipping suite %s", suite)
                continue

            # Apply final parallelism (may be higher than baseline if scaling was applied)
            final_parallelism = final_jobs.get(suite)
            if final_parallelism is not None and final_parallelism > 1:
                if jobspec.parallelism != final_parallelism:
                    LOGGER.info("Suite %s: parallelism=%d", suite, final_parallelism)
                jobspec.parallelism = final_parallelism
            elif jobspec.parallelism is None and (final_parallelism is None or final_parallelism <= 1):
                pass  # leave as None (GitLab default: single job)

            print(str(jobspec), file=f)

            if shadow_enabled:
                shadow_config = suites[suite].copy()
                shadow_config.pop("_stage", None)
                shadow_config.pop("_clean_name", None)
                shadow_config["env"] = dict(shadow_config.get("env") or {})
                shadow_config["env"]["SUITE_NAME"] = shadow_config.get("pattern", clean_name)
                shadow_config["env"]["CI_ALLOCATION_STRATEGY"] = "balanced"
                shadow_config["parallelism"] = balanced_jobs[suite]
                shadow_config["allow_failure"] = True
                shadow_jobspec = JobSpec(
                    f"{clean_name}-allocation-shadow",
                    stage=stage,
                    python_versions=py_versions,
                    **shadow_config,
                )
                print(str(shadow_jobspec), file=f)


def gen_build_docs() -> None:
    """Include the docs build step if the docs have changed."""
    from needs_testrun import pr_matches_patterns

    if pr_matches_patterns(
        {
            "docs/*",
            "ddtrace/*",
            "scripts/docs/*",
            "scripts/gen_gitlab_config.py",
            "benchmarks/README.rst",
            ".readthedocs.yml",
        }
    ):
        # build_docs uses Python 3.10; ensure it's included in build_base_venvs
        _global_python_versions.add("3.10")

        with TESTS_GEN.open("a") as f:
            print("build_docs:", file=f)
            print("  extends: .testrunner", file=f)
            print("  stage: core", file=f)
            print("  needs:", file=f)
            print("    - prechecks", file=f)
            print("    - job: build_base_venvs", file=f)
            print("      artifacts: true", file=f)
            print("      parallel:", file=f)
            print("        matrix:", file=f)
            print('          - PYTHON_VERSION: "3.10"', file=f)
            print("  script:", file=f)
            print("    - |", file=f)
            print("      git config --global --add safe.directory $CI_PROJECT_DIR", file=f)
            print("      riot -v run -s --pass-env build_docs", file=f)
            print("      mkdir -p /tmp/docs", file=f)
            print("  artifacts:", file=f)
            print("    paths:", file=f)
            print("      - 'docs/'", file=f)


def gen_pre_checks() -> None:
    """Generate the list of pre-checks that need to be run."""
    from needs_testrun import pr_matches_patterns

    checks: list[tuple[str, str]] = []

    def check(name: str, command: str, paths: set[str]) -> None:
        if pr_matches_patterns(paths):
            checks.append((name, command))

    check(
        name="Style",
        command="scripts/lint style",
        paths={"docker*", "*.py", "*.pyi", "pyproject.toml", "*.cpp", "*.h", "scripts/lint"},
    )
    check(
        name="Typing",
        command="scripts/lint typing",
        paths={"docker*", "*.py", "*.pyi", "pyproject.toml", "mypy.ini", "scripts/lint"},
    )
    check(
        name="Spelling",
        command="scripts/lint spelling",
        paths={"*"},
    )
    check(
        name="Security",
        command="scripts/lint security",
        paths={"docker*", "ddtrace/*", "pyproject.toml", "scripts/lint"},
    )
    check(
        name="Run riotfile.py tests",
        command="scripts/lint riot",
        paths={"docker*", "riotfile.py", "pyproject.toml", "scripts/lint"},
    )
    check(
        name="Style: Test snapshots",
        command="scripts/lint fmt-snapshots && git diff --exit-code tests/snapshots",
        paths={"docker*", "tests/snapshots/*", "scripts/lint"},
    )
    check(
        name="Run scripts/*.py tests",
        command="scripts/run-script-doctests.py",
        paths={"docker*", "scripts/*.py", "scripts/run-test-suite", "**suitespec.yml"},
    )
    check(
        name="Check suitespec coverage",
        command="scripts/lint suitespec-check",
        paths={"*"},
    )
    check(
        name="Check CI allocation contract",
        command="scripts/ci_allocation_cli.py check-contract",
        paths={
            ".gitlab/*",
            "ci/ci-allocation-*.json",
            "scripts/ci_allocation/*",
            "scripts/ci_allocation_cli.py",
            "scripts/gen_gitlab_config.py",
            "tests/suitespec.py",
            "tests/**/suitespec.yml",
        },
    )
    check(
        name="Check ddtrace error logs",
        command="scripts/lint error-log-check",
        paths={"ddtrace/*", "scripts/check_constant_log_message.py", "scripts/lint"},
    )
    check(
        name="Check profiling native coverage",
        command="scripts/lint profiling-native-check",
        paths={
            ".gitlab-ci.yml",
            "ddtrace/internal/datadog/profiling/*",
            "ddtrace/profiling/*",
            "scripts/check_profiling_native_coverage.py",
            "scripts/gen_gitlab_config.py",
            "scripts/lint",
            "src/native/*",
        },
    )
    check(
        name="Check project dependencies",
        command="scripts/check-dependency-bounds && scripts/check-dependency-ci-coverage.py",
        paths={"pyproject.toml", "riotfile.py", ".gitlab-ci.yml", ".gitlab/**/*.yml", ".github/workflows/*.yml"},
    )
    check(
        name="Check package version",
        command="scripts/verify-package-version",
        paths={"pyproject.toml"},
    )
    check(
        name="Check for namespace packages",
        command="scripts/check-for-namespace-packages.sh",
        paths={"*"},
    )
    check(
        name="Hook tests",
        command="scripts/lint hook-tests",
        paths={"hooks/scripts/*.sh", "hooks/pre-commit/*", "hooks/tests/*", "scripts/lint"},
    )
    if not checks:
        return

    with TESTS_GEN.open("a") as f:
        f.write(
            """
prechecks:
  extends: .testrunner
  stage: setup
  needs: []
  variables:
    PIP_CACHE_DIR: '${CI_PROJECT_DIR}/.cache/pip'
  script:
    - |
      echo -e "\\e[0Ksection_start:`date +%s`:pip_cache_info[collapsed=true]\\r\\e[0KPip cache info"
      pip cache info
      echo -e "\\e[0Ksection_end:`date +%s`:pip_cache_info\\r\\e[0K"
        """
        )
        for i, (name, command) in enumerate(checks):
            f.write(
                rf"""
    - |
      echo -e "\e[0Ksection_start:`date +%s`:section_{i}[collapsed=true]\\r\\e[0K{name}"
      {command}
      echo -e "\e[0Ksection_end:`date +%s`:section_{i}\\r\\e[0K"
            """
            )
        f.write(
            """
  cache:
    key: v2-precheck-pip-cache
    paths:
      - .cache
"""
        )


def gen_cached_testrunner() -> None:
    """Generate the cached testrunner job."""
    with TESTS_GEN.open("a") as f:
        f.write(
            template(
                "cached-testrunner",
                current_week=datetime.datetime.now().isocalendar().week,
                testrunner_image_hash=TESTRUNNER_IMAGE_HASH,
            )
        )


def gen_build_base_venvs() -> None:
    """Generate the list of base jobs for building virtual environments.

    We need to generate this dynamically from a template because it depends
    on the cached testrunner job, which is also generated dynamically.

    Only builds venvs for the Python versions actually needed by the required suites,
    falling back to all supported versions when no venv info is available.
    """
    if _global_python_versions:
        py_versions = sorted(_global_python_versions)
        LOGGER.info("Building base venvs for Python versions: %s", py_versions)
    else:
        py_versions = ALL_PYTHON_VERSIONS
        LOGGER.info("No suite venv info available, building all Python versions: %s", py_versions)

    python_versions_str = ", ".join(f'"{v}"' for v in py_versions)

    with TESTS_GEN.open("a") as f:
        f.write(
            template(
                "build-base-venvs",
                python_versions=python_versions_str,
                unpin_dependencies=_get_bool_env("UNPIN_DEPENDENCIES"),
                nightly_build=_get_bool_env("NIGHTLY_BUILD"),
            )
        )


# -----------------------------------------------------------------------------

# The code below is the boilerplate that makes the script work. There is
# generally no reason to modify it.

import logging  # noqa
import sys  # noqa
from argparse import ArgumentParser  # noqa
from pathlib import Path  # noqa
from time import monotonic_ns as time  # noqa


logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
LOGGER = logging.getLogger(__name__)

argp = ArgumentParser()
argp.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
argp.add_argument("--debug", "-d", action="store_true", help="Debug output")
argp.add_argument(
    "--suite",
    action="append",
    dest="suites",
    default=[],
    metavar="SUITE",
    help="Only generate jobs for this suite (can be repeated). Bypasses PR/file detection.",
)
argp.add_argument(
    "--file",
    action="append",
    dest="files",
    default=[],
    metavar="FILE",
    help="Treat this file as changed for suite/precheck detection (can be repeated). Bypasses PR detection.",
)
args = argp.parse_args()
if args.debug:
    LOGGER.setLevel(logging.DEBUG)
elif args.verbose:
    LOGGER.setLevel(logging.INFO)

# If --file args are provided, inject them into needs_testrun so gen_build_docs and
# gen_pre_checks also use the supplied files instead of querying GitHub.
if args.files:
    import needs_testrun as _needs_testrun

    _needs_testrun._changed_files_override = set(args.files)

ROOT = Path(__file__).parents[1]
GITLAB = ROOT / ".gitlab"
TESTS = ROOT / "tests"
TESTS_GEN = GITLAB / "tests-gen.yml"
CI_ALLOCATION_PLAN = GITLAB / "ci-allocation-plan.json"
CI_ALLOCATION_MODEL = ROOT / "ci" / "ci-allocation-runtime-model.json"
CI_ALLOCATION_POLICY = ROOT / "ci" / "ci-allocation-policy.json"
MICROBENCHMARKS_GEN = GITLAB / "benchmarks/microbenchmarks-gen.yml"
MICROBENCHMARKS_SLOS = GITLAB / "benchmarks/bp-runner.microbenchmarks.fail-on-breach.yml"
MICROBENCHMARKS_SLOS_TEMPLATE = GITLAB / "benchmarks/bp-runner.microbenchmarks.fail-on-breach.template.yml"

# Compute a short hash of the testrunner image so cache keys are automatically
# invalidated whenever the image changes (e.g. Python patch version bumps).
import ruamel.yaml as _ruamel_yaml  # noqa: E402


_testrunner_yaml = _ruamel_yaml.YAML().load((GITLAB / "testrunner.yml").read_text())
TESTRUNNER_IMAGE_HASH = hashlib.sha256(_testrunner_yaml["variables"]["TESTRUNNER_IMAGE"].encode()).hexdigest()[:16]
# Make the project root, scripts, and tests folders available for importing.
sys.path.append(str(ROOT))
sys.path.append(str(ROOT / "scripts"))
sys.path.append(str(ROOT / "tests"))

from ci_allocation.history import load_json  # noqa: E402
from ci_allocation.history import runtime_estimates  # noqa: E402
from ci_allocation.manifest import build_allocation_manifest  # noqa: E402
from ci_allocation.manifest import write_manifest  # noqa: E402
from ci_allocation.suites import calculate_parallelism_from_venvs  # noqa: E402,F401
from ci_allocation.suites import collect_all_suite_venv_info  # noqa: E402
from ci_allocation.suites import compute_parallelism  # noqa: E402
from ci_allocation.suites import compute_runtime_parallelism  # noqa: E402
from ci_allocation.suites import scale_suites as _scale_suites  # noqa: E402,F401


def template(name: str, **params):
    """Render a template file with the given parameters."""
    if not (template_path := (GITLAB / "templates" / name).with_suffix(".yml")).exists():
        raise FileNotFoundError(f"Template file {template_path} does not exist")
    return "\n" + template_path.read_text().format(**params).strip() + "\n"


if __name__ == "__main__":
    has_error = False

    LOGGER.info("Configuration generation steps:")
    for name, func in dict(globals()).items():
        if name.startswith("gen_"):
            desc = func.__doc__.splitlines()[0]
            try:
                start = time()
                func()
                LOGGER.info("- %s: %s [took %dms]", name, desc, int((time() - start) / 1e6))
            except Exception as e:
                LOGGER.error("- %s: %s [reason: %s]", name, desc, str(e), exc_info=True)
                has_error = True

    sys.exit(has_error)
