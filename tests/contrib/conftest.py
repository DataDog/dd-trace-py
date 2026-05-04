import pathlib
import sys

import pytest


# Add the project root to sys.path so scripts.supported_version is importable.
_project_root = str(pathlib.Path(__file__).parent.parent.parent)
if _project_root not in sys.path:
    sys.path.append(_project_root)

from scripts.supported_version.dependency_name_updater import dependency_name_updater  # noqa: E402


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
def update_dependency_names_at_end():
    """Processes accumulated registry data at the end of the session."""
    yield  # allows test session to run

    try:
        dependency_name_updater.update_from_patched_objects()
    except Exception as e:
        print(f"\nCritical error during dependency name update: {e}", file=sys.stderr)
    finally:
        dependency_name_updater.cleanup_post_session()
