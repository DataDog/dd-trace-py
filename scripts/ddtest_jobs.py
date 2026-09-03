"""ddtest job emission for gen_gitlab_config.py.

All ddtest-specific logic is encapsulated here so that removing ddtest support
is a simple matter of deleting this file and reverting the import + call sites
in gen_gitlab_config.py, the ddtest templates in .gitlab/tests.yml, and the
ddtest: true flags in suitespec.yml.

Functions:
  - validate_ddtest_venv_test_locations: reject suites without DDTEST_TESTS_LOCATION
  - ddtest_k: read per-venv CI node count from suitespec
  - emit_ddtest_jobs: emit plan + run jobs for one ddtest suite
"""

import subprocess
import typing as t


def _get_bool_env(name: str) -> str:
    """Return "true"/"false" for a boolean environment variable."""
    import logging
    import os

    LOGGER = logging.getLogger(__name__)
    value = os.getenv(name, "").lower()
    if value not in ("", "true", "false"):
        LOGGER.warning("Ignoring unexpected value for %s, treating it as false", name)
    return "true" if value == "true" else "false"


def validate_ddtest_venv_test_locations(
    suite: str, environments: t.Iterable[tuple[str, str]], test_locations: t.Optional[dict[str, str]]
) -> None:
    """Reject ddtest suites whose selected environments lack a test location."""
    missing = [h for h, _py in environments if not (test_locations or {}).get(h)]
    if missing:
        raise ValueError(f"ddtest suite {suite} has venvs without DDTEST_TESTS_LOCATION: {', '.join(missing)}")


def ddtest_k(config: dict) -> int:
    """Per-venv CI node count K for a ddtest suite.

    K controls file splitting WITHIN a venv (a different axis from the
    legacy parallelism/venvs_per_job, which controlled venv PACKING — how
    many venvs per CI job). ddtest already fans out one job per venv via the
    parallel matrix; K further splits each venv's files across K CI
    nodes.

    K=1 (default): each venv runs all its files in one job (closest to
    legacy semantics, where each job ran all files for its venv). K=2:
    each venv's files split into 2 groups → 2x jobs per venv.

    A suite can override K with a `ddtest_nodes` suitespec key. If not
    set, K defaults to 1 (no file splitting). The legacy `parallelism`/
    `venvs_per_job` knobs are NOT used for K — they controlled venv packing,
    a different axis.
    """
    k = config.get("ddtest_nodes")
    if k is None or k < 1:
        k = 1
    return int(k)


def _ddtest_base(snapshot: bool, gpu: bool, runner: str) -> str:
    """Return the hidden base template a ddtest suite's before_script references."""
    base = ".ddtest_base_uv" if runner == "uv" else ".ddtest_base"
    if gpu:
        base += "_gpu"
    if snapshot:
        base += "_snapshot"
    return base


def _ddtest_plan_template(gpu: bool, runner: str) -> str:
    """Plan template. Plan only collects (glob + Datadog API); it sends
    no traces, so it never needs the testagent and has no snapshot
    variant — snapshot suites and non-snapshot suites plan the same way.
    """
    tpl = ".ddtest_plan_uv" if runner == "uv" else ".ddtest_plan"
    if gpu:
        tpl += "_gpu"
    return tpl


def _ddtest_run_template(snapshot: bool, gpu: bool, runner: str) -> str:
    tpl = ".ddtest_run_uv" if runner == "uv" else ".ddtest_run"
    if gpu:
        tpl += "_gpu"
    if snapshot:
        tpl += "_snapshot"
    return tpl


def emit_ddtest_jobs(
    f,
    suite: str,
    stage: str,
    clean_name: str,
    config: dict,
    environments: list[tuple[str, str]],
    k: int,
    testrunner_image_hash: str,
    runner: str,
    uv_metadata: t.Optional[dict[str, tuple[str, str, str, str]]] = None,
) -> None:
    """Emit ddtest-plan and ddtest-run jobs for one suite.

    One plan job loops over all suite environments. Run jobs are emitted per Python
    version, with a parallel matrix over that version's hashes and
    CI_NODE_INDEX. The plan partitions its artifact by hash so run jobs can
    restore only their own plan.
    """
    snapshot = config.get("snapshot", False)
    gpu = config.get("gpu", False)
    services = list(dict.fromkeys(config.get("services") or []))
    env = dict(config.get("env") or {})
    retry = config.get("retry")
    timeout = config.get("timeout")
    allow_failure = config.get("allow_failure", False)
    skip_pip_cache = config.get("skip_pip_cache", False)
    suite_name = config.get("pattern") or clean_name
    env["PIP_CACHE_DIR"] = "${CI_PROJECT_DIR}/.cache/pip"
    env["PIP_CACHE_KEY"] = (
        subprocess.check_output([".gitlab/scripts/get-riot-pip-cache-key.sh", suite_name]).decode().strip()
    )
    base = _ddtest_base(snapshot, gpu, runner)
    plan_base = _ddtest_base(False, gpu, runner)  # plan never needs the testagent
    plan_tpl = _ddtest_plan_template(gpu, runner)
    run_tpl = _ddtest_run_template(snapshot, gpu, runner)
    job_prefix = f"{stage}/{clean_name.replace('::', '/')}"
    plan_name = f"{job_prefix}::ddtest-plan"
    run_name = f"{job_prefix}::ddtest-run"
    wait_for = list(services)
    if snapshot:
        wait_for.append("testagent")

    def emit_services(plan: bool) -> None:
        if not services:
            return
        print("  services:", file=f)
        svc_base = plan_base if plan else base
        _svc = [f"!reference [.services, {s}]" for s in services]
        if snapshot and not plan:
            _svc.insert(0, f"!reference [{svc_base}, services]")
        for s in _svc:
            print(f"    - {s}", file=f)

    def emit_before_script(plan: bool) -> None:
        print("  before_script:", file=f)
        ref_base = plan_base if plan else base
        print(f"    - !reference [{ref_base}, before_script]", file=f)
        print("    - pip cache info", file=f)
        print(f'    - export NIGHTLY_BUILD="{_get_bool_env("NIGHTLY_BUILD")}"', file=f)
        # Plan only collects; it sends no traces, so it never waits for the
        # testagent even for snapshot suites.
        if wait_for and not plan:
            print(f"    - riot -v run -s --pass-env wait -- {' '.join(wait_for)}", file=f)

    def emit_variables(extra: t.Optional[dict[str, str]] = None) -> None:
        print("  variables:", file=f)
        print(f"    SUITE_NAME: {suite_name}", file=f)
        for key, value in env.items():
            print(f"    {key}: {value}", file=f)
        if extra:
            for key, value in extra.items():
                print(f"    {key}: {value}", file=f)

    def emit_cache() -> None:
        if skip_pip_cache:
            return
        print("  cache:", file=f)
        print(f"    key: v1-pip-${{PIP_CACHE_KEY}}-{testrunner_image_hash}-cache", file=f)
        print("    paths:", file=f)
        print("      - .cache", file=f)

    def emit_needs_build_base_venvs(needed_environments: list[tuple[str, str]]) -> None:
        print("    - job: build_base_venvs", file=f)
        print("      artifacts: true", file=f)
        print("      parallel:", file=f)
        print("        matrix:", file=f)
        # Dedup PYTHON_VERSIONs: several hashes share a Python version, but
        # build_base_venvs only needs to be downloaded once per version.
        seen_py: set[str] = set()
        for _h, py in needed_environments:
            if py in seen_py:
                continue
            seen_py.add(py)
            print(f'          - PYTHON_VERSION: "{py}"', file=f)

    # ---- plan job: single job per suite (groups hashes by Python version) ----
    # One plan job per suite (not per venv) to reduce CI runner contention.
    # The job prepares and plans hashes in parallel across Python versions,
    # partitioning the plan artifact by hash.
    print(f"{plan_name}:", file=f)
    print(f"  extends: {plan_tpl}", file=f)
    print(f"  stage: {stage}", file=f)
    emit_cache()
    print("  needs:", file=f)
    print("    - prechecks", file=f)
    emit_needs_build_base_venvs(environments)
    emit_services(plan=True)
    emit_before_script(plan=True)
    metadata = uv_metadata or {}
    hash_python = " ".join(
        f"{h}:{py}:{metadata[h][0]}:{metadata[h][1]}" if runner == "uv" else f"{h}:{py}" for h, py in environments
    )
    extra_variables = {
        "DDTEST_NODES": str(k),
        "TEST_ENVIRONMENT_HASH_PYTHON": hash_python,
        "DD_TEST_OPTIMIZATION_RUNNER_COMMAND": "pytest",
    }
    if runner == "uv":
        for hash_, (_lockfile, _location, command, environment) in metadata.items():
            extra_variables[f"DDTEST_UV_COMMAND_{hash_}"] = command
            extra_variables[f"DDTEST_UV_ENV_{hash_}"] = environment
    emit_variables(extra_variables)
    if retry is not None:
        print(f"  retry: {retry}", file=f)
    if timeout is not None:
        print(f"  timeout: {timeout}", file=f)
    if allow_failure:
        print("  allow_failure: true", file=f)
    # artifacts (.testoptimization-*/) are declared on the .ddtest_plan template.

    # ---- run jobs: K instances per venv, grouped by Python version ----
    # Matrix expressions are not available on all GitLab versions used by CI,
    # so emit one run job per Python version instead of dynamically matching a
    # need from the run matrix. This keeps each run job's artifact download
    # limited to its own build_base_venvs matrix entry.
    environments_by_python: dict[str, list[tuple[str, str]]] = {}
    for environment in environments:
        environments_by_python.setdefault(environment[1], []).append(environment)

    for py, python_environments in environments_by_python.items():
        py_run_name = f"{run_name}-{py}"
        print(f"{py_run_name}:", file=f)
        print(f"  extends: {run_tpl}", file=f)
        print(f"  stage: {stage}", file=f)
        emit_cache()
        print("  needs:", file=f)
        print("    - prechecks", file=f)
        emit_needs_build_base_venvs(python_environments)
        # Each run downloads the single plan artifact (which contains all
        # hashes' plans, partitioned by hash) and restores its own hash's plan.
        print("    - job: " + plan_name, file=f)
        print("      artifacts: true", file=f)
        emit_services(plan=False)
        emit_before_script(plan=False)
        run_variables = {"DD_TEST_OPTIMIZATION_RUNNER_COMMAND": "pytest"}
        if runner == "uv":
            for hash_, (_lockfile, _location, command, environment) in metadata.items():
                run_variables[f"DDTEST_UV_COMMAND_{hash_}"] = command
                run_variables[f"DDTEST_UV_ENV_{hash_}"] = environment
        emit_variables(run_variables)
        print("  parallel:", file=f)
        print("    matrix:", file=f)
        for h, _py in python_environments:
            for node in range(k):
                print(f'      - TEST_ENVIRONMENT_HASH: "{h}"', file=f)
                print(f'        PYTHON_VERSION: "{py}"', file=f)
                if runner == "uv":
                    print(f'        TEST_ENVIRONMENT_LOCKFILE: "{metadata[h][0]}"', file=f)
                print(f"        CI_NODE_INDEX: {node}", file=f)
        if retry is not None:
            print(f"  retry: {retry}", file=f)
        if timeout is not None:
            print(f"  timeout: {timeout}", file=f)
        if allow_failure:
            print("  allow_failure: true", file=f)
