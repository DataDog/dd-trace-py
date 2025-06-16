# This script is used to generate the GitLab dynamic config file in
# .gitlab/tests.yml.
#
# To add new configuration manipulations that are based on top of the template
# file in .gitlab/tests.yml, add a function named gen_<name> to this
# file. The function will be called automatically when this script is run.

from dataclasses import dataclass
import os
import subprocess
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
    allow_failure: bool = False
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
            lines.append("    key: v0-pip-${PIP_CACHE_KEY}-cache")
            lines.append("    paths:")
            lines.append("      - .cache")
        else:
            lines.append("  cache:")
            lines.append("    key: v0-${CI_JOB_NAME}-pip-cache")
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

    # If the ci_visibility suite is in the list of required suites, we need to run all suites
    ci_visibility_suites = {"ci_visibility", "pytest", "pytest_v2"}
    # If any of them in required_suites:
    if any(suite in required_suites for suite in ci_visibility_suites):
        required_suites = sorted(suites.keys())

    # Copy the template file
    TESTS_GEN.write_text((GITLAB / "tests.yml").read_text())
    # Generate the list of suites to run
    with TESTS_GEN.open("a") as f:
        for suite in required_suites:
            jobspec = JobSpec(suite, **suites[suite])
            if jobspec.skip:
                LOGGER.debug("Skipping suite %s", suite)
                continue

            print(str(jobspec), file=f)


def gen_build_docs() -> None:
    """Include the docs build step if the docs have changed."""
    from needs_testrun import pr_matches_patterns

    if pr_matches_patterns(
        {"docker*", "docs/*", "ddtrace/*", "scripts/docs/*", "releasenotes/*", "benchmarks/README.rst"}
    ):
        with TESTS_GEN.open("a") as f:
            print("build_docs:", file=f)
            print("  extends: .testrunner", file=f)
            print("  stage: hatch", file=f)
            print("  needs: []", file=f)
            print("  variables:", file=f)
            print("    PIP_CACHE_DIR: '${CI_PROJECT_DIR}/.cache/pip'", file=f)
            print("  script:", file=f)
            print("    - |", file=f)
            print("      hatch run docs:build", file=f)
            print("      mkdir -p /tmp/docs", file=f)
            print("  cache:", file=f)
            print("    key: v1-build_docs-pip-cache", file=f)
            print("    paths:", file=f)
            print("      - .cache", file=f)
            print("  artifacts:", file=f)
            print("    paths:", file=f)
            print("      - 'docs/'", file=f)


def gen_pre_checks() -> None:
    """Generate the list of pre-checks that need to be run."""
    from needs_testrun import pr_matches_patterns

    checks: list[tuple[str, str]] = []

    def check(name: str, command: str, paths: t.Set[str]) -> None:
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
    key: v1-precheck-pip-cache
    paths:
      - .cache
        """
        )


def gen_build_base_venvs() -> None:
    """Generate component-specific build jobs for virtual environments and native extensions."""

    ci_commit_sha = os.getenv("CI_COMMIT_SHA", "default")
    # Try to get component-specific hashes, fall back to legacy single hash
    try:
        from component_config import ComponentConfig
        from get_component_hashes import generate_component_hashes

        component_hashes = generate_component_hashes()
        all_components = ComponentConfig.get_all_components()
    except (ImportError, Exception):
        # Fall back to legacy behavior - single monolithic job
        native_hash = os.getenv("DD_NATIVE_SOURCES_HASH", ci_commit_sha)
        _gen_legacy_build_base_venvs(native_hash)
        return

    with TESTS_GEN.open("a") as f:
        # Generate base venv job (Python dependencies, no native extensions)
        f.write(
            """
build_base_venv:
  extends: .testrunner
  stage: setup
  needs: []
  parallel:
    matrix:
      - PYTHON_VERSION: ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13"]
  variables:
    PIP_CACHE_DIR: '${CI_PROJECT_DIR}/.cache/pip'
    DD_BUILD_EXT_EXCLUDES: '*'  # Exclude all native extensions
  script: |
    set -e -o pipefail
    echo "Building base Python environment (no native extensions)"
    pip install riot==0.20.1
    riot -P -v generate --python=$PYTHON_VERSION
    echo "Base venv created successfully"
  cache:
    key: v1-base_venv-${PYTHON_VERSION}-cache
    paths:
      - .cache
  artifacts:
    name: base_venv_$PYTHON_VERSION
    paths:
      - .riot/venv_*
      - ddtrace/_version.py
    expire_in: 2 hours
"""
        )

        # Generate component-specific build jobs
        for component in all_components:
            component_hash = component_hashes.get(component, ci_commit_sha)[:16]

            # Define component-specific patterns and artifacts
            if ComponentConfig.is_cmake_component(component):
                build_patterns = _get_cmake_build_patterns(component)
                artifact_patterns = _get_cmake_artifact_patterns(component)
                build_deps = ["apt update && apt install -y sccache cmake"]
            elif ComponentConfig.is_rust_component(component):
                build_patterns = _get_rust_build_patterns(component)
                artifact_patterns = _get_rust_artifact_patterns(component)
                build_deps = [
                    "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y",
                    "source ~/.cargo/env",
                ]
            else:  # Python components (Cython, C extensions)
                build_patterns = _get_python_build_patterns(component)
                artifact_patterns = _get_python_artifact_patterns(component)
                build_deps = ["apt update && apt install -y build-essential"]

            f.write(
                f"""
build_{component}:
  extends: .testrunner
  stage: setup
  needs:
    - job: build_base_venv
      artifacts: true
  parallel:
    matrix:
      - PYTHON_VERSION: ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13"]
  variables:
    CMAKE_BUILD_PARALLEL_LEVEL: '12'
    DD_USE_SCCACHE: '1'
    PIP_CACHE_DIR: '${{CI_PROJECT_DIR}}/.cache/pip'
    SCCACHE_DIR: '${{CI_PROJECT_DIR}}/.cache/sccache'
    DD_BUILD_EXT_INCLUDES: '{build_patterns}'
    DD_FAST_BUILD: '1'
  rules:
    - if: '$CI_COMMIT_REF_NAME == "main"'
      variables:
        DD_FAST_BUILD: '0'
    - when: always
  script: |
    set -e -o pipefail
    echo "Building component: {component}"
    {chr(10).join(build_deps)}

    # Check if we can use cached artifacts
    if python3 scripts/build_cache.py check {component} 2>/dev/null; then
      echo "Using cached artifacts for {component}"
      python3 scripts/build_cache.py restore {component} || echo "Cache restore failed, will rebuild"
    fi

    # Build only this component's extensions
    pip install riot==0.20.1
    riot -P -v generate --python=$PYTHON_VERSION

    echo "Component {component} built successfully"
  cache:
    # Component-specific cache
    - key: v1-{component}-${{PYTHON_VERSION}}-{component_hash}
      paths:
        - .cache
        - .build_cache/py${{PYTHON_VERSION}}/{ComponentConfig.get_component_cache_dir_name(component)}
  artifacts:
    name: {component}_$PYTHON_VERSION
    paths:
{chr(10).join(f"      - {pattern}" for pattern in artifact_patterns)}
    expire_in: 2 hours
"""
            )

        # Generate final assembly job that combines all components
        f.write(
            f"""
build_complete_venv:
  extends: .testrunner
  stage: setup
  needs:
    - job: build_base_venv
      artifacts: true
{chr(10).join(f"    - job: build_{component}" + chr(10) + "      artifacts: true" for component in all_components)}
  parallel:
    matrix:
      - PYTHON_VERSION: ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13"]
  script: |
    set -e -o pipefail
    echo "Assembling complete virtual environment from components"

    # All artifacts are already in place from dependencies
    # Run smoke test to verify everything works
    pip install riot==0.20.1
    riot -v run -s --python=$PYTHON_VERSION smoke_test

    echo "Complete environment assembled and tested successfully"
  artifacts:
    name: venv_$PYTHON_VERSION
    paths:
      - .riot/venv_*
      - ddtrace/_version.py
      - ddtrace/**/*.so*
      - ddtrace/internal/datadog/profiling/crashtracker/crashtracker_exe*
      - ddtrace/internal/datadog/profiling/test/test_*
    expire_in: 1 day
"""
        )


def _gen_legacy_build_base_venvs(native_hash: str) -> None:
    """Generate legacy monolithic build job as fallback."""
    with TESTS_GEN.open("a") as f:
        f.write(
            f"""
build_base_venvs:
  extends: .testrunner
  stage: setup
  needs: []
  parallel:
    matrix:
      - PYTHON_VERSION: ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13"]
  variables:
    CMAKE_BUILD_PARALLEL_LEVEL: '12'
    PIP_VERBOSE: '0'
    DD_PROFILING_NATIVE_TESTS: '1'
    DD_USE_SCCACHE: '1'
    PIP_CACHE_DIR: '${{CI_PROJECT_DIR}}/.cache/pip'
    SCCACHE_DIR: '${{CI_PROJECT_DIR}}/.cache/sccache'
    DD_FAST_BUILD: '1'
  script: |
    set -e -o pipefail
    echo "Building complete environment (legacy mode)"
    apt update && apt install -y sccache
    pip install riot==0.20.1
    riot -P -v generate --python=$PYTHON_VERSION
    riot -v run -s --python=$PYTHON_VERSION smoke_test
  cache:
    key: v1-build_base_venvs-${{PYTHON_VERSION}}-{native_hash[:16]}
    paths:
      - .cache
      - .riot/venv_*
      - ddtrace/**/*.so*
  artifacts:
    name: venv_$PYTHON_VERSION
    paths:
      - .riot/venv_*
      - ddtrace/_version.py
      - ddtrace/**/*.so*
      - ddtrace/internal/datadog/profiling/crashtracker/crashtracker_exe*
      - ddtrace/internal/datadog/profiling/test/test_*
"""
        )


def _get_cmake_build_patterns(component: str) -> str:
    """Get build patterns for CMake components."""
    patterns = {
        "cmake_iast": "ddtrace.appsec._iast._taint_tracking._native",
        "cmake_ddup": "ddtrace.internal.datadog.profiling.ddup._ddup",
        "cmake_crashtracker": "ddtrace.internal.datadog.profiling.crashtracker._crashtracker",
        "cmake_stack_v2": "ddtrace.internal.datadog.profiling.stack_v2._stack_v2",
    }
    return patterns.get(component, "*cmake*")


def _get_cmake_artifact_patterns(component: str) -> list:
    """Get artifact patterns for CMake components."""
    try:
        from component_config import ComponentConfig

        cache_dir_name = ComponentConfig.get_component_cache_dir_name(component)
    except ImportError:
        cache_dir_name = component

    base_patterns = [f".build_cache/py${{PYTHON_VERSION}}/{cache_dir_name}"]

    if component == "cmake_iast":
        return base_patterns + ["ddtrace/appsec/_iast/_taint_tracking/**/*.so*"]
    elif component == "cmake_ddup":
        return base_patterns + ["ddtrace/internal/datadog/profiling/ddup/**/*.so*"]
    elif component == "cmake_crashtracker":
        return base_patterns + [
            "ddtrace/internal/datadog/profiling/crashtracker/**/*.so*",
            "ddtrace/internal/datadog/profiling/crashtracker/crashtracker_exe*",
        ]
    elif component == "cmake_stack_v2":
        return base_patterns + ["ddtrace/internal/datadog/profiling/stack_v2/**/*.so*"]
    else:
        return base_patterns


def _get_rust_build_patterns(component: str) -> str:
    """Get build patterns for Rust components."""
    return "ddtrace.internal.native._native"


def _get_rust_artifact_patterns(component: str) -> list:
    """Get artifact patterns for Rust components."""
    try:
        from component_config import ComponentConfig

        cache_dir_name = ComponentConfig.get_component_cache_dir_name(component)
    except ImportError:
        cache_dir_name = component

    return [f".build_cache/py${{PYTHON_VERSION}}/{cache_dir_name}", "target/", "ddtrace/internal/native/**/*.so*"]


def _get_python_build_patterns(component: str) -> str:
    """Get build patterns for Python components."""
    if component == "cython_extensions":
        return (
            "ddtrace.internal._rand,ddtrace.internal._tagset,ddtrace.internal._encoding,ddtrace.profiling.collector.*"
        )
    elif component == "c_extensions":
        return "ddtrace.profiling.collector._memalloc,ddtrace.internal._threads,ddtrace.appsec._iast._stacktrace"
    elif component == "vendor_extensions":
        return "ddtrace.vendor.*"
    else:
        return "*"


def _get_python_artifact_patterns(component: str) -> list:
    """Get artifact patterns for Python components."""
    try:
        from component_config import ComponentConfig

        cache_dir_name = ComponentConfig.get_component_cache_dir_name(component)
    except ImportError:
        cache_dir_name = component

    base_patterns = [f".build_cache/py${{PYTHON_VERSION}}/{cache_dir_name}"]

    if component == "cython_extensions":
        return base_patterns + [
            "ddtrace/internal/_rand*.so*",
            "ddtrace/internal/_tagset*.so*",
            "ddtrace/internal/_encoding*.so*",
            "ddtrace/profiling/collector/stack*.so*",
            "ddtrace/profiling/collector/_traceback*.so*",
            "ddtrace/profiling/_threading*.so*",
            "ddtrace/profiling/collector/_task*.so*",
        ]
    elif component == "c_extensions":
        return base_patterns + [
            "ddtrace/profiling/collector/_memalloc*.so*",
            "ddtrace/internal/_threads*.so*",
            "ddtrace/appsec/_iast/_stacktrace*.so*",
            "ddtrace/appsec/_iast/_ast/iastpatch*.so*",
        ]
    elif component == "vendor_extensions":
        return base_patterns + ["ddtrace/vendor/**/*.so*"]
    else:
        return base_patterns


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
