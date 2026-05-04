import pathlib
import sys

import pytest


# Add the project root to sys.path so scripts.supported_version is importable.
_project_root = str(pathlib.Path(__file__).parent.parent.parent)
if _project_root not in sys.path:
    sys.path.append(_project_root)

from scripts.supported_version.dependency_name_updater import dependency_name_updater  # noqa: E402


_MISSING_TESTED_VERSIONS_KEY = "_missing_tested_versions"


@pytest.fixture(scope="function", autouse=True)
def manage_registry_patching(request):
    """Conditionally patches getattr before each test and cleans up patch afterwards."""
    should_patch = not request.node.get_closest_marker("no_getattr_patch")
    if should_patch:
        dependency_name_updater.patch_getattr()

    yield  # allows test function to run

    # Always restore getattr and clear per-test state
    dependency_name_updater.cleanup_patch()


@pytest.fixture(scope="session", autouse=True)
def update_dependency_names_at_end(request):
    """Processes accumulated registry data at the end of the session."""
    yield  # allows test session to run

    missing_tested_versions = []
    try:
        session_tested_versions = dependency_name_updater.collect_session_tested_versions()
        dependency_name_updater.update_from_patched_objects()
        missing_tested_versions = dependency_name_updater.get_missing_tested_versions(session_tested_versions)
    except Exception as e:
        print(f"\nCritical error during dependency name update: {e}", file=sys.stderr)
    finally:
        dependency_name_updater.cleanup_post_session()

    setattr(request.config, _MISSING_TESTED_VERSIONS_KEY, missing_tested_versions)


def _format_missing_versions(missing_tested_versions):
    missing_versions_lines = [
        "  - {integration_name}: {dependency_name}=={version} on Python {python_version}".format(**tested_version)
        for tested_version in missing_tested_versions[:20]
    ]
    if len(missing_tested_versions) > 20:
        missing_versions_lines.append(f"  ... and {len(missing_tested_versions) - 20} more")
    return missing_versions_lines


@pytest.hookimpl(trylast=True)
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    missing_tested_versions = getattr(config, _MISSING_TESTED_VERSIONS_KEY, [])
    if not missing_tested_versions:
        return

    terminalreporter.section("missing tested dependency versions", sep="=")
    terminalreporter.write_line(
        "Tested dependency versions are missing from ci_tested_versions.json. "
        "Run scripts/compile-and-prune-test-requirements to regenerate it."
    )
    for line in _format_missing_versions(missing_tested_versions):
        terminalreporter.write_line(line)


def pytest_sessionfinish(session, exitstatus):
    missing_tested_versions = getattr(session.config, _MISSING_TESTED_VERSIONS_KEY, [])
    if missing_tested_versions and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
