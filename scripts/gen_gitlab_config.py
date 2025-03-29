# This script is used to generate the GitLab dynamic config file in
# .gitlab/tests.yml.
#
# To add new configuration manipulations that are based on top of the template
# file in .gitlab/tests.yml, add a function named gen_<name> to this
# file. The function will be called automatically when this script is run.

from dataclasses import dataclass
import typing as t


@dataclass
class JobSpec:
    name: str
    runner: str
    pattern: t.Optional[str] = None
    snapshot: bool = False
    services: t.Optional[t.List[str]] = None
    env: t.Optional[t.Dict[str, str]] = None
    parallelism: t.Optional[int] = None
    retry: t.Optional[int] = None
    timeout: t.Optional[int] = None
    skip: bool = False
    paths: t.Optional[t.Set[str]] = None  # ignored
    only: t.Optional[t.Set[str]] = None  # ignored

    def __str__(self) -> str:
        lines = []
        base = f".test_base_{self.runner}"
        if self.snapshot:
            base += "_snapshot"

        lines.append(f"{self.name}:")
        lines.append(f"  extends: {base}")

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
        if wait_for:
            lines.append("  before_script:")
            lines.append(f"    - !reference [{base}, before_script]")
            if self.runner == "riot" and wait_for:
                lines.append(f"    - riot -v run -s --pass-env wait -- {' '.join(wait_for)}")

        env = self.env
        if not env or "SUITE_NAME" not in env:
            env = env or {}
            env["SUITE_NAME"] = self.pattern or self.name

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

        return "\n".join(lines)


def gen_required_suites() -> None:
    """Generate the list of test suites that need to be run."""
    from needs_testrun import extract_git_commit_selections
    from needs_testrun import for_each_testrun_needed
    import suitespec

    suites = suitespec.get_suites()

    required_suites: t.List[str] = []

    for_each_testrun_needed(
        suites=sorted(suites.keys()),
        action=lambda suite: required_suites.append(suite),
        git_selections=extract_git_commit_selections(os.getenv("CI_COMMIT_MESSAGE", "")),
    )

    # Exclude the suites that are run in CircleCI. These likely don't run in
    # GitLab yet.
    with YAML() as yaml:
        circleci_config = yaml.load(ROOT / ".circleci" / "config.templ.yml")
        circleci_jobs = set(circleci_config["jobs"].keys())

    # Copy the template file
    TESTS_GEN.write_text((GITLAB / "tests.yml").read_text())
    # Generate the list of suites to run
    with TESTS_GEN.open("a") as f:
        for suite in required_suites:
            if suite.rsplit("::", maxsplit=1)[-1] in circleci_jobs:
                LOGGER.debug("Skipping CircleCI suite %s", suite)
                continue

            jobspec = JobSpec(suite, **suites[suite])
            if jobspec.skip:
                LOGGER.debug("Skipping suite %s", suite)
                continue

            print(str(jobspec), file=f)


def gen_build_docs() -> None:
    """Include the docs build step if the docs have changed."""
    from needs_testrun import pr_matches_patterns

    if pr_matches_patterns(
        {"docker*", "docs/*", "ddtrace/*", "scripts/docs", "releasenotes/*", "benchmarks/README.rst"}
    ):
        with TESTS_GEN.open("a") as f:
            print("build_docs:", file=f)
            print("  extends: .testrunner", file=f)
            print("  stage: hatch", file=f)
            print("  needs: []", file=f)
            print("  script:", file=f)
            print("    - |", file=f)
            print("      hatch run docs:build", file=f)
            print("      mkdir -p /tmp/docs", file=f)
            print("      cp -r docs/_build/html/* /tmp/docs", file=f)
            print("  artifacts:", file=f)
            print("    paths:", file=f)
            print("      - '/tmp/docs'", file=f)


def gen_pre_checks() -> None:
    """Generate the list of pre-checks that need to be run."""
    from needs_testrun import pr_matches_patterns

    def check(name: str, command: str, paths: t.Set[str]) -> None:
        if pr_matches_patterns(paths):
            with TESTS_GEN.open("a") as f:
                print(f'"{name}":', file=f)
                print("  extends: .testrunner", file=f)
                print("  stage: precheck", file=f)
                print("  needs: []", file=f)
                print("  script:", file=f)
                print(f"    - {command}", file=f)

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
        paths={"docker*", "scripts/*.py", "scripts/mkwheelhouse", "scripts/run-test-suite", "**suitespec.yml"},
    )
    check(
        name="Check suitespec coverage",
        command="hatch run lint:suitespec-check",
        paths={"*"},
    )


def gen_appsec_iast_packages() -> None:
    """Generate the list of jobs for the appsec_iast_packages tests."""
    with TESTS_GEN.open("a") as f:
        f.write(
            """
appsec_iast_packages:
  extends: .test_base_hatch
  timeout: 50m
  parallel:
    matrix:
      - PYTHON_VERSION: ["3.9", "3.10", "3.11", "3.12"]
  variables:
    CMAKE_BUILD_PARALLEL_LEVEL: '12'
    PIP_VERBOSE: '0'
    PIP_CACHE_DIR: '${CI_PROJECT_DIR}/.cache/pip'
    PYTEST_ADDOPTS: '-s'
  cache:
    # Share pip between jobs of the same Python version
      key: v1.2-appsec_iast_packages-${PYTHON_VERSION}-cache
      paths:
        - .cache
  before_script:
    - !reference [.test_base_hatch, before_script]
    - pyenv global "${PYTHON_VERSION}"
  script:
    - export PYTEST_ADDOPTS="${PYTEST_ADDOPTS} --ddtrace"
    - export DD_FAST_BUILD="1"
    - hatch run appsec_iast_packages.py${PYTHON_VERSION}:test
        """
        )


# -----------------------------------------------------------------------------

# The code below is the boilerplate that makes the script work. There is
# generally no reason to modify it.

import logging  # noqa
import os  # noqa
import sys  # noqa
from argparse import ArgumentParser  # noqa
from pathlib import Path  # noqa
from time import monotonic_ns as time  # noqa

from ruamel.yaml import YAML  # noqa

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
# Make the scripts and tests folders available for importing.
sys.path.append(str(ROOT / "scripts"))
sys.path.append(str(ROOT / "tests"))

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
