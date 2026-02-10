#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "riot>=0.21.0",
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
import os
import re
import subprocess
import typing as t


MAX_BENCHMARKS_PER_GROUP = 8
BENCHMARK_CLASS_REGEX = r"class ([A-Za-z]+)\((bm\.)?Scenario(.+)?\)\:"
BENCHMARK_SCENARIO_REGEX = re.compile(" +- name: ([a-z0-9]+)-.+")


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
    runner: str
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

    def __str__(self) -> str:
        lines = []
        base = f".test_base_{self.runner}"
        if self.gpu:
            base += "_gpu"
        if self.snapshot:
            base += "_snapshot"

        lines.append(f"{self.stage}/{self.name.replace('::', '/')}:")
        lines.append(f"  extends: {base}")

        # Set stage
        lines.append(f"  stage: {self.stage}")

        # Set needs based on runner type
        lines.append("  needs:")
        lines.append("    - prechecks")
        if self.runner == "riot":
            # Riot jobs need build_base_venvs artifacts
            lines.append("    - job: build_base_venvs")
            lines.append("      artifacts: true")

        services = set(self.services or [])
        if services:
            lines.append("  services:")

            _services = [f"!reference [.services, {_}]" for _ in services]
            if self.snapshot:
                _services.insert(0, f"!reference [{base}, services]")

            for service in _services:
                lines.append(f"    - {service}")

        wait_for = services.copy()
        if self.snapshot:
            wait_for.add("testagent")

        lines.append("  before_script:")
        lines.append(f"    - !reference [{base}, before_script]")
        lines.append("    - pip cache info")
        if wait_for:
            if self.runner == "riot" and wait_for:
                lines.append(f"    - riot -v run -s --pass-env wait -- {' '.join(wait_for)}")

        env = self.env
        if not env or "SUITE_NAME" not in env:
            env = env or {}
            env["SUITE_NAME"] = self.pattern or self.name

        suite_name = env["SUITE_NAME"]
        env["PIP_CACHE_DIR"] = "${CI_PROJECT_DIR}/.cache/pip"
        if self.runner == "riot":
            env["PIP_CACHE_KEY"] = (
                subprocess.check_output([".gitlab/scripts/get-riot-pip-cache-key.sh", suite_name]).decode().strip()
            )
            lines.append("  cache:")
            lines.append("    key: v1-pip-${PIP_CACHE_KEY}-cache")
            lines.append("    paths:")
            lines.append("      - .cache")
        else:
            lines.append("  cache:")
            lines.append("    key: v1-${CI_JOB_NAME}-pip-cache")
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


def calculate_dynamic_parallelism(suite_name: str, suite_config: dict) -> t.Optional[int]:
    """Calculate parallelism based on venvs_per_job configuration.

    Packs N venvs per parallel job, scaling automatically with venv count changes.
    Only applies to riot runner suites with venvs_per_job configured.

    Args:
        suite_name: The name of the test suite
        suite_config: The suite configuration dict from suitespec

    Returns:
        The calculated parallelism value (1 to 20), or None if venvs_per_job not configured
    """
    # Only for riot suites
    if suite_config.get("runner") != "riot":
        return None

    # Check if venvs_per_job is configured
    venvs_per_job = suite_config.get("venvs_per_job")
    if venvs_per_job is None:
        return None

    # Only import when needed
    import math

    # Importing will load/evaluate the whole riotfile.py
    import riotfile

    pattern = suite_config.get("pattern", suite_name)
    try:
        pattern_regex = re.compile(pattern)
    except re.error:
        LOGGER.warning("Invalid pattern for suite %s: %s", suite_name, pattern)
        return None

    # Collect unique venv hashes by matching pattern (mimics riot's --hash-only logic)
    venv_hashes = set()
    for inst in riotfile.venv.instances():  # type: ignore[attr-defined]
        if not inst.name or not inst.matches_pattern(pattern_regex):  # type: ignore[attr-defined]
            continue
        venv_hashes.add(inst.short_hash)  # type: ignore[attr-defined]

    venv_count = len(venv_hashes)

    if venv_count == 0:
        LOGGER.warning("No riot venvs found for suite %s with pattern %s", suite_name, pattern)
        return None

    # Calculate parallelism
    calculated = math.ceil(venv_count / venvs_per_job)

    # Cap at 20 to avoid over-parallelization
    MAX_PARALLELISM = 20
    calculated = min(calculated, MAX_PARALLELISM)

    LOGGER.debug(
        "Suite %s: %d venvs, %d venvs_per_job -> parallelism %d",
        suite_name,
        venv_count,
        venvs_per_job,
        calculated,
    )
    return calculated


def gen_required_suites() -> None:
    """Generate the list of test and benchmark suites that need to be run."""
    from needs_testrun import extract_git_commit_selections
    from needs_testrun import for_each_testrun_needed
    import suitespec

    suites = suitespec.get_suites()

    required_suites: list[str] = []

    for_each_testrun_needed(
        suites=sorted(suites.keys()),
        action=lambda suite: required_suites.append(suite),
        git_selections=extract_git_commit_selections(os.getenv("CI_COMMIT_MESSAGE", "")),
    )

    # If the ci_visibility suite is in the list of required suites, we need to run all suites
    ci_visibility_suites = {"ci_visibility", "pytest"}
    # If any of them in required_suites:
    if any(suite in required_suites for suite in ci_visibility_suites):
        required_suites = sorted(suites.keys())

    _gen_tests(suites, required_suites)
    _gen_benchmarks(suites, required_suites)


def _gen_benchmarks(suites: dict, required_suites: list[str]) -> None:
    suites = {k: v for k, v in suites.items() if "benchmark" in v.get("type", "test")}
    required_suites = [a for a in required_suites if a in list(suites.keys())]
    required_suites = []

    if not required_suites:
        MICROBENCHMARKS_GEN.write_text("")
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
    contents = MICROBENCHMARKS_SLOS_TEMPLATE.read_text()

    for line in contents.split("\n")[1:]:
        match = re.match(BENCHMARK_SCENARIO_REGEX, line)
        if match:
            class_on_line = match.group(1)
            if class_on_line in classnames:
                in_scenario_to_keep = True
            else:
                in_scenario_to_keep = False
        if line.strip().startswith("#"):
            in_scenario_to_keep = False
        if in_scenario_to_keep:
            new_contents.append(line)

    MICROBENCHMARKS_SLOS.write_text("\n".join(new_contents))


def _gen_tests(suites: dict, required_suites: list[str]) -> None:
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

    # Generate the list of suites to run
    with TESTS_GEN.open("a") as f:
        for suite in required_suites:
            suite_config = suites[suite].copy()
            # Extract stage and clean name from config
            stage = suite_config.pop("_stage", "core")
            clean_name = suite_config.pop("_clean_name", suite)

            # Create JobSpec with clean name and explicit stage
            jobspec = JobSpec(clean_name, stage=stage, **suite_config)
            if jobspec.skip:
                LOGGER.debug("Skipping suite %s", suite)
                continue

            # Calculate dynamic parallelism for riot suites without explicit value
            if jobspec.parallelism is None and suite_config.get("runner") == "riot":
                calculated = calculate_dynamic_parallelism(suite, suite_config)
                if calculated is not None:
                    jobspec.parallelism = calculated
                    LOGGER.info("Suite %s: calculated parallelism=%d", suite, calculated)
                else:
                    LOGGER.debug("Suite %s: no venvs_per_job config, using GitLab default", suite)

            print(str(jobspec), file=f)


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
        with TESTS_GEN.open("a") as f:
            print("build_docs:", file=f)
            print("  extends: .testrunner", file=f)
            print("  stage: core", file=f)
            print("  needs:", file=f)
            print("    - prechecks", file=f)
            print("    - job: build_base_venvs", file=f)
            print("      artifacts: true", file=f)
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
        command="hatch run lint:style",
        paths={"docker*", "*.py", "*.pyi", "hatch.toml", "pyproject.toml", "*.cpp", "*.h"},
    )
    check(
        name="Typing",
        command="hatch run lint:typing",
        paths={"docker*", "*.py", "*.pyi", "hatch.toml", "mypy.ini"},
    )
    check(
        name="Security",
        command="hatch run lint:security",
        paths={"docker*", "ddtrace/*", "hatch.toml"},
    )
    check(
        name="Run riotfile.py tests",
        command="hatch run lint:riot",
        paths={"docker*", "riotfile.py", "hatch.toml"},
    )
    check(
        name="Style: Test snapshots",
        command="hatch run lint:fmt-snapshots && git diff --exit-code tests/snapshots hatch.toml",
        paths={"docker*", "tests/snapshots/*", "hatch.toml"},
    )
    check(
        name="Run scripts/*.py tests",
        command="hatch run scripts:test",
        paths={"docker*", "scripts/*.py", "scripts/run-test-suite", "**suitespec.yml"},
    )
    check(
        name="Check suitespec coverage",
        command="hatch run lint:suitespec-check",
        paths={"*"},
    )
    check(
        name="Check ddtrace error logs",
        command="hatch run lint:error-log-check",
        paths={"ddtrace/*", "scripts/check_constant_log_message.py"},
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
        f.write(template("cached-testrunner", current_month=datetime.datetime.now().month))


def gen_build_base_venvs() -> None:
    """Generate the list of base jobs for building virtual environments.

    We need to generate this dynamically from a template because it depends
    on the cached testrunner job, which is also generated dynamically.
    """
    with TESTS_GEN.open("a") as f:
        f.write(template("build-base-venvs", unpin_dependencies=os.getenv("UNPIN_DEPENDENCIES", "false") or "false"))


def gen_debugger_exploration() -> None:
    """Generate the cached testrunner job.

    We need to generate this dynamically from a template because it depends
    on the cached testrunner job, which is also generated dynamically.
    """
    from needs_testrun import pr_matches_patterns

    if not pr_matches_patterns(
        {
            ".gitlab/templates/debugging/exploration.yml",
            "ddtrace/debugging/*",
            "ddtrace/internal/bytecode_injection/__init__.py",
            "ddtrace/internal/wrapping/context.py",
            "tests/debugging/exploration/*",
        }
    ):
        return

    with TESTS_GEN.open("a") as f:
        f.write(template("debugging/exploration"))


def gen_detect_global_locks() -> None:
    """Generate the global lock detection job."""
    with TESTS_GEN.open("a") as f:
        f.write(template("detect-global-locks"))


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
args = argp.parse_args()
if args.debug:
    LOGGER.setLevel(logging.DEBUG)
elif args.verbose:
    LOGGER.setLevel(logging.INFO)

ROOT = Path(__file__).parents[1]
GITLAB = ROOT / ".gitlab"
TESTS = ROOT / "tests"
TESTS_GEN = GITLAB / "tests-gen.yml"
MICROBENCHMARKS_GEN = GITLAB / "benchmarks/microbenchmarks-gen.yml"
MICROBENCHMARKS_SLOS = GITLAB / "benchmarks/bp-runner.microbenchmarks.fail-on-breach.yml"
MICROBENCHMARKS_SLOS_TEMPLATE = GITLAB / "benchmarks/bp-runner.microbenchmarks.fail-on-breach.template.yml"
# Make the scripts and tests folders available for importing.
sys.path.append(str(ROOT / "scripts"))
sys.path.append(str(ROOT / "tests"))


def template(name: str, **params):
    """Render a template file with the given parameters."""
    if not (template_path := (GITLAB / "templates" / name).with_suffix(".yml")).exists():
        raise FileNotFoundError(f"Template file {template_path} does not exist")
    return "\n" + template_path.read_text().format(**params).strip() + "\n"


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
