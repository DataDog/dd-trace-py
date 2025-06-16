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
    """Generate 4-cache build jobs based on product areas and dependencies."""

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
        # Generate dependency hash for Python environment
        dependency_hash = _get_dependency_hash()

        # 1. RIOT DEPENDENCIES (test requirements)
        f.write(
            f"""
build_base_venv:
  extends: .testrunner
  stage: setup
  needs: []
  parallel:
    matrix:
      - PYTHON_VERSION: ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13"]
  variables:
    PIP_CACHE_DIR: '${{CI_PROJECT_DIR}}/.cache/pip'
    DD_BUILD_EXT_EXCLUDES: '*'  # Exclude all native extensions
  script: |
    set -e -o pipefail
    echo "Building riot test dependencies (no native extensions)"
    echo "Dependency hash: {dependency_hash[:16]}"
    pip install riot==0.20.1
    riot -P -v generate --python=$PYTHON_VERSION
    echo "Riot dependencies created successfully"
  cache:
    # Cache based on dependency files (riot requirements)
    - key: v1-riot_deps-${{PYTHON_VERSION}}-{dependency_hash[:16]}
      paths:
        - .cache
        - .riot/venv_*
  artifacts:
    name: riot_deps_$PYTHON_VERSION
    paths:
      - .riot/venv_*
      - ddtrace/_version.py
    expire_in: 72 hours  # Longer expiry for dependencies
"""
        )

        # 2. PROFILING NATIVE BUILD (ddtrace.internal.profiling)
        profiling_components = ["cmake_ddup", "cmake_crashtracker", "cmake_stack_v2", "rust_native"]
        profiling_hash = _get_combined_hash([component_hashes.get(c, ci_commit_sha) for c in profiling_components])
        profiling_patterns = [
            "ddtrace.internal.datadog.profiling.ddup._ddup",
            "ddtrace.internal.datadog.profiling.crashtracker._crashtracker",
            "ddtrace.internal.datadog.profiling.stack_v2._stack_v2",
            "ddtrace.internal.native._native",
        ]

        f.write(
            f"""
build_profiling_native:
  extends: .testrunner
  stage: setup
  needs:
    - job: build_riot_dependencies
      artifacts: true
  parallel:
    matrix:
      - PYTHON_VERSION: ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13"]
  variables:
    CMAKE_BUILD_PARALLEL_LEVEL: '12'
    DD_USE_SCCACHE: '1'
    PIP_CACHE_DIR: '${{CI_PROJECT_DIR}}/.cache/pip'
    SCCACHE_DIR: '${{CI_PROJECT_DIR}}/.cache/sccache'
    DD_BUILD_EXT_INCLUDES: '{",".join(profiling_patterns)}'
    DD_FAST_BUILD: '1'
  rules:
    - if: '$CI_COMMIT_REF_NAME == "main"'
      variables:
        DD_FAST_BUILD: '0'
    - when: always
  script: |
    set -e -o pipefail
    echo "Building profiling native components (hash: {profiling_hash[:16]})"
    apt update && apt install -y sccache cmake
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source ~/.cargo/env

    # Check if we can use cached artifacts
    if python3 scripts/build_cache.py check cmake_ddup 2>/dev/null; then
      echo "Using cached artifacts for profiling components"
      python3 scripts/build_cache.py restore cmake_ddup || echo "Cache restore failed, will rebuild"
      python3 scripts/build_cache.py restore cmake_crashtracker || echo "Cache restore failed, will rebuild"
      python3 scripts/build_cache.py restore cmake_stack_v2 || echo "Cache restore failed, will rebuild"
      python3 scripts/build_cache.py restore rust_native || echo "Cache restore failed, will rebuild"
    fi

    # Build profiling extensions
    pip install riot==0.20.1
    riot -P -v generate --python=$PYTHON_VERSION

    echo "Profiling native components built successfully"
  cache:
    # Cache based on profiling source code hash
    - key: v1-profiling_native-${{PYTHON_VERSION}}-{profiling_hash[:16]}
      paths:
        - .cache
        - .build_cache/py${{PYTHON_VERSION}}/cmake_ddup
        - .build_cache/py${{PYTHON_VERSION}}/cmake_crashtracker
        - .build_cache/py${{PYTHON_VERSION}}/cmake_stack_v2
        - .build_cache/py${{PYTHON_VERSION}}/rust_native
        - target/
  artifacts:
    name: profiling_native_$PYTHON_VERSION
    paths:
      - .build_cache/py${{PYTHON_VERSION}}/cmake_ddup
      - .build_cache/py${{PYTHON_VERSION}}/cmake_crashtracker
      - .build_cache/py${{PYTHON_VERSION}}/cmake_stack_v2
      - .build_cache/py${{PYTHON_VERSION}}/rust_native
      - ddtrace/internal/datadog/profiling/ddup/**/*.so*
      - ddtrace/internal/datadog/profiling/crashtracker/**/*.so*
      - ddtrace/internal/datadog/profiling/crashtracker/crashtracker_exe*
      - ddtrace/internal/datadog/profiling/stack_v2/**/*.so*
      - ddtrace/internal/native/**/*.so*
      - target/
    expire_in: 24 hours """
        )

        # 3. IAST NATIVE BUILD (ddtrace.appsec._iast)
        iast_hash = component_hashes.get("cmake_iast", ci_commit_sha)[:16]

        f.write(
            f"""
build_iast_native:
  extends: .testrunner
  stage: setup
  needs:
    - job: build_riot_dependencies
      artifacts: true
  parallel:
    matrix:
      - PYTHON_VERSION: ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13"]
  variables:
    CMAKE_BUILD_PARALLEL_LEVEL: '12'
    DD_USE_SCCACHE: '1'
    PIP_CACHE_DIR: '${{CI_PROJECT_DIR}}/.cache/pip'
    SCCACHE_DIR: '${{CI_PROJECT_DIR}}/.cache/sccache'
    DD_BUILD_EXT_INCLUDES: 'ddtrace.appsec._iast._taint_tracking._native'
    DD_FAST_BUILD: '1'
  rules:
    - if: '$CI_COMMIT_REF_NAME == "main"'
      variables:
        DD_FAST_BUILD: '0'
    - when: always
  script: |
    set -e -o pipefail
    echo "Building IAST native component (hash: {iast_hash})"
    apt update && apt install -y sccache cmake

    # Check if we can use cached artifacts
    if python3 scripts/build_cache.py check cmake_iast 2>/dev/null; then
      echo "Using cached artifacts for IAST component"
      python3 scripts/build_cache.py restore cmake_iast || echo "Cache restore failed, will rebuild"
    fi

    # Build IAST extension
    pip install riot==0.20.1
    riot -P -v generate --python=$PYTHON_VERSION

    echo "IAST native component built successfully"
  cache:
    # Cache based on IAST source code hash
    - key: v1-iast_native-${{PYTHON_VERSION}}-{iast_hash}
      paths:
        - .cache
        - .build_cache/py${{PYTHON_VERSION}}/cmake_iast
  artifacts:
    name: iast_native_$PYTHON_VERSION
    paths:
      - .build_cache/py${{PYTHON_VERSION}}/cmake_iast
      - ddtrace/appsec/_iast/_taint_tracking/**/*.so*
    expire_in: 24 hours
"""
        )

        # 4. BASE DDTRACE BUILD (everything else: hatch.toml + setup.py + cython + vendor + etc.)
        base_components = ["cython_extensions", "c_extensions", "vendor_extensions"]
        base_hash = _get_combined_hash(
            [component_hashes.get(c, ci_commit_sha) for c in base_components] + [dependency_hash]
        )
        base_patterns = [
            "ddtrace.internal._rand,ddtrace.internal._tagset,ddtrace.internal._encoding,ddtrace.profiling.collector.*",
            "ddtrace.profiling.collector._memalloc,ddtrace.internal._threads,ddtrace.appsec._iast._stacktrace",
            "ddtrace.vendor.*",
        ]

        f.write(
            f"""
build_base_ddtrace:
  extends: .testrunner
  stage: setup
  needs:
    - job: build_riot_dependencies
      artifacts: true
  parallel:
    matrix:
      - PYTHON_VERSION: ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13"]
  variables:
    CMAKE_BUILD_PARALLEL_LEVEL: '12'
    DD_USE_SCCACHE: '1'
    PIP_CACHE_DIR: '${{CI_PROJECT_DIR}}/.cache/pip'
    SCCACHE_DIR: '${{CI_PROJECT_DIR}}/.cache/sccache'
    DD_BUILD_EXT_INCLUDES: '{",".join(base_patterns)}'
    DD_FAST_BUILD: '1'
  rules:
    - if: '$CI_COMMIT_REF_NAME == "main"'
      variables:
        DD_FAST_BUILD: '0'
    - when: always
  script: |
    set -e -o pipefail
    echo "Building base ddtrace components (hash: {base_hash[:16]})"
    apt update && apt install -y build-essential

    # Check if we can use cached artifacts
    if python3 scripts/build_cache.py check cython_extensions 2>/dev/null; then
      echo "Using cached artifacts for base ddtrace components"
      python3 scripts/build_cache.py restore cython_extensions || echo "Cache restore failed, will rebuild"
      python3 scripts/build_cache.py restore c_extensions || echo "Cache restore failed, will rebuild"
      python3 scripts/build_cache.py restore vendor_extensions || echo "Cache restore failed, will rebuild"
    fi

    # Build base ddtrace extensions
    pip install riot==0.20.1
    riot -P -v generate --python=$PYTHON_VERSION

    echo "Base ddtrace components built successfully"
  cache:
    # Cache based on base ddtrace source + dependency hash
    - key: v1-base_ddtrace-${{PYTHON_VERSION}}-{base_hash[:16]}
      paths:
        - .cache
        - .build_cache/py${{PYTHON_VERSION}}/cython
        - .build_cache/py${{PYTHON_VERSION}}/c_extensions
        - .build_cache/py${{PYTHON_VERSION}}/vendor
  artifacts:
    name: base_ddtrace_$PYTHON_VERSION
    paths:
      - .build_cache/py${{PYTHON_VERSION}}/cython
      - .build_cache/py${{PYTHON_VERSION}}/c_extensions
      - .build_cache/py${{PYTHON_VERSION}}/vendor
      - ddtrace/internal/_rand*.so*
      - ddtrace/internal/_tagset*.so*
      - ddtrace/internal/_encoding*.so*
      - ddtrace/profiling/collector/stack*.so*
      - ddtrace/profiling/collector/_traceback*.so*
      - ddtrace/profiling/_threading*.so*
      - ddtrace/profiling/collector/_task*.so*
      - ddtrace/profiling/collector/_memalloc*.so*
      - ddtrace/internal/_threads*.so*
      - ddtrace/appsec/_iast/_stacktrace*.so*
      - ddtrace/appsec/_iast/_ast/iastpatch*.so*
      - ddtrace/vendor/**/*.so*
    expire_in: 24 hours
"""
        )

        # Generate final assembly job that combines all 4 caches
        f.write(
            f"""
build_complete_venv:
  extends: .testrunner
  stage: setup
  needs:
    - job: build_riot_dependencies
      artifacts: true
    - job: build_profiling_native
      artifacts: true
    - job: build_iast_native
      artifacts: true
    - job: build_base_ddtrace
      artifacts: true
  parallel:
    matrix:
      - PYTHON_VERSION: ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13"]
  script: |
    set -e -o pipefail
    echo "Assembling complete virtual environment from 4 build caches"

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
      - target/
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


def _get_dependency_hash() -> str:
    """Generate hash based on dependency files (riotfile.py, riot requirements, etc.)."""
    import hashlib

    dependency_files = [
        "riotfile.py",
        "pyproject.toml",
        "hatch.toml",
    ]

    hasher = hashlib.sha256()

    # Add Python version to the hash since dependencies can be Python-version specific
    import sys

    hasher.update(f"python-{sys.version_info.major}.{sys.version_info.minor}".encode())

    # Hash main dependency files
    for dep_file in dependency_files:
        file_path = Path(dep_file)
        if file_path.exists():
            hasher.update(dep_file.encode())
            try:
                with open(file_path, "rb") as f:
                    hasher.update(f.read())
            except (OSError, IOError):
                pass  # Skip files that can't be read

    # Hash all riot requirements files
    riot_requirements_dir = Path(".riot/requirements")
    if riot_requirements_dir.exists():
        # Get all requirement files in the riot directory
        for req_file in sorted(riot_requirements_dir.glob("**/*.txt")):
            hasher.update(str(req_file.relative_to(Path("."))).encode())
            try:
                with open(req_file, "rb") as f:
                    hasher.update(f.read())
            except (OSError, IOError):
                pass  # Skip files that can't be read

    return hasher.hexdigest()


def _get_combined_hash(hashes: list) -> str:
    """Combine multiple hashes into a single hash."""
    import hashlib

    combined = "".join(str(h) for h in hashes)
    return hashlib.sha256(combined.encode()).hexdigest()


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
