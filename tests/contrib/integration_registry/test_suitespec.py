import fnmatch
import re

from mappings import EXCLUDED_FROM_TESTING
from mappings import INTEGRATION_TO_DEPENDENCY_MAPPING
from packaging.requirements import InvalidRequirement
from packaging.requirements import Requirement
import pytest

from ddtrace.internal.compat import MAX_PY
from tests import suitespec
from tests.suitespec import DEFAULT_PYTHON_VERSIONS
from tests.suitespec import get_patterns
from tests.suitespec import get_test_environments


COVERAGE_UPLOAD_ENV = "DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED"


@pytest.fixture
def coverage_suite_config():
    return {
        "matrix": {
            "command": "pytest {cmdargs} tests/example",
            "runs": [{"env": {"PYTHONOPTIMIZE": "0"}}, {"env": {"PYTHONOPTIMIZE": "1"}}],
            "variants": [{"name": "example", "python": list(DEFAULT_PYTHON_VERSIONS)}],
        }
    }


@pytest.mark.parametrize("python", DEFAULT_PYTHON_VERSIONS)
@pytest.mark.parametrize("nightly", [False, True])
def test_nightly_coverage_report_upload_python_selection(coverage_suite_config, python, nightly):
    environments = suitespec._expand_suite_matrix("example", coverage_suite_config, nightly=nightly)
    environment = next(environment for environment in environments if environment.python == python)

    assert len(environment.runs) == 2
    for index, run in enumerate(environment.runs):
        assert run.environment["PYTHONOPTIMIZE"] == str(index)
        if nightly:
            assert run.environment[COVERAGE_UPLOAD_ENV] == ("1" if python == "3.15" else "0")
        else:
            assert COVERAGE_UPLOAD_ENV not in run.environment


@pytest.mark.parametrize("scope", ["matrix", "variant", "run"])
@pytest.mark.parametrize("value", ["0", "false"])
def test_nightly_coverage_report_upload_preserves_opt_out(coverage_suite_config, scope, value):
    matrix = coverage_suite_config["matrix"]
    target = {"matrix": matrix, "variant": matrix["variants"][0], "run": matrix["runs"][0]}[scope]
    target.setdefault("env", {})[COVERAGE_UPLOAD_ENV] = value

    environments = suitespec._expand_suite_matrix("example", coverage_suite_config, nightly=True)
    environment = next(environment for environment in environments if environment.python == "3.15")

    assert environment.runs[0].environment[COVERAGE_UPLOAD_ENV] == value
    assert environment.runs[1].environment[COVERAGE_UPLOAD_ENV] == ("1" if scope == "run" else value)


def test_nightly_coverage_report_upload_does_not_change_environment_identity(coverage_suite_config):
    regular = suitespec._expand_suite_matrix("example", coverage_suite_config, nightly=False)
    nightly = suitespec._expand_suite_matrix("example", coverage_suite_config, nightly=True)

    assert len(regular) == len(nightly)
    for regular_environment, nightly_environment in zip(regular, nightly):
        assert regular_environment.hash == nightly_environment.hash
        assert regular_environment.lockfile == nightly_environment.lockfile
        assert regular_environment.direct_dependencies == nightly_environment.direct_dependencies
        for regular_run, nightly_run in zip(regular_environment.runs, nightly_environment.runs):
            nightly_env = nightly_run.environment
            nightly_env.pop(COVERAGE_UPLOAD_ENV)
            assert regular_run.environment == nightly_env
            assert regular_run.command == nightly_run.command


def test_nightly_coverage_report_upload_versions_can_be_extended(coverage_suite_config, monkeypatch):
    monkeypatch.setattr(suitespec, "NIGHTLY_COVERAGE_PYTHON_VERSIONS", ("3.12", "3.15"))

    environments = suitespec._expand_suite_matrix("example", coverage_suite_config, nightly=True)

    for environment in environments:
        for run in environment.runs:
            assert run.environment[COVERAGE_UPLOAD_ENV] == ("1" if environment.python in ("3.12", "3.15") else "0")


def test_python_versions_follow_compat_bounds():
    environments = get_test_environments(nightly=False)

    # MAX_PY is the last packaged CPython (requires-python). Schedule it in the
    # default matrix. NEXT_MAX_PY is wrap-live only until the testrunner image
    # ships that interpreter — do not require it in DEFAULT yet.
    assert f"{MAX_PY[0]}.{MAX_PY[1]}" in DEFAULT_PYTHON_VERSIONS
    assert DEFAULT_PYTHON_VERSIONS[-1] == f"{MAX_PY[0]}.{MAX_PY[1]}"
    assert f"{MAX_PY[0]}.{MAX_PY[1]}" in {environment.python for environment in environments["smoke_test"]}


def test_integrations_have_suitespec_environments(
    integration_dir_names: set[str],
    untested_integrations: set[str],
):
    environments = get_test_environments(nightly=False)
    patterns = {
        pattern
        for suite, suite_environments in environments.items()
        if suite_environments
        for pattern in get_patterns(suite)
    }
    missing_environments = {
        integration
        for integration in integration_dir_names - untested_integrations
        if not any(
            fnmatch.fnmatch(f"ddtrace/contrib/internal/{integration}/__init__.py", pattern) for pattern in patterns
        )
    }

    assert not missing_environments, "Integrations missing a suitespec environment: " + ", ".join(
        sorted(missing_environments)
    )


def test_contrib_environments_use_valid_integration_names(integration_dir_names: set[str]):
    environments = get_test_environments(nightly=False)
    invalid_environments = [
        environment
        for suite, suite_environments in environments.items()
        if suite.startswith("contrib::")
        for environment in suite_environments
        if any("tests/contrib" in run.command for run in environment.runs)
        and environment.integration_name not in integration_dir_names
        and environment.integration_name not in EXCLUDED_FROM_TESTING
    ]

    assert not invalid_environments, "Contrib environments use unknown integration names: " + ", ".join(
        environment.name for environment in invalid_environments
    )


# Integrations whose package cannot be tested unpinned, with the reason.
LATEST_NOT_TESTED = {
    "algoliasearch": "only capped versions are tested",
    "botocore": "SigV4 propagation tests fail on the latest botocore (traceparent missing from SignedHeaders)",
    "claude_agent_sdk": "PyPI deletes old releases, so unpinned locks go stale",
    "pydantic_ai": "only capped versions are tested",
    "ray": "ray tests fail on the latest ray (local_mode is no longer supported)",
    "ray_serve": "ray_serve tests fail on the latest ray",
    "vertica": "only capped versions are tested",
}


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_every_integration_tests_latest_package_version(registry_content):
    """Each integration needs a suitespec environment with its package unpinned (or floor-only), so the
    nightly lock bump tests the newest release. Pinned-only integrations go in LATEST_NOT_TESTED.
    """
    tested_latest = set()
    seen = set()
    for environments in get_test_environments(nightly=False).values():
        for environment in environments:
            integration = environment.integration_name
            if integration in EXCLUDED_FROM_TESTING:
                continue
            names = {_canonical(n) for n in INTEGRATION_TO_DEPENDENCY_MAPPING.get(integration) or {integration}}
            names.add(_canonical(integration))
            for raw in environment.direct_dependencies:
                try:
                    requirement = Requirement(raw)
                except InvalidRequirement:
                    continue
                if _canonical(requirement.name) not in names:
                    continue
                seen.add(integration)
                if {s.operator for s in requirement.specifier} <= {">=", ">"}:
                    tested_latest.add(integration)

    registry = {entry["integration_name"]: entry for entry in registry_content["integrations"]}
    tracked = {name for name, entry in registry.items() if entry["is_external_package"] and entry["is_tested"]} - set(
        EXCLUDED_FROM_TESTING
    )
    unmatched = tracked - seen
    assert not unmatched, (
        f"no environment lists the package of {sorted(unmatched)} as a direct dependency, so its version "
        "coverage cannot be checked; fix the dependency name mapping"
    )
    unknown = LATEST_NOT_TESTED.keys() - registry.keys() - seen
    assert not unknown, f"remove from LATEST_NOT_TESTED, no such integration: {sorted(unknown)}"

    missing = seen - tested_latest - LATEST_NOT_TESTED.keys()
    assert not missing, (
        f"no environment tests the latest package version for {sorted(missing)}; unpin one or allowlist it"
    )
    stale = LATEST_NOT_TESTED.keys() & tested_latest
    assert not stale, f"remove from LATEST_NOT_TESTED, latest is now tested: {sorted(stale)}"
