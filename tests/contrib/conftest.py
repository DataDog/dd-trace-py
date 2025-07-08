import os
import sys

import pytest

from tests.contrib.integration_registry.registry_update_helpers.integration_registry_manager import registry_manager
from tests.contrib.integration_registry.registry_update_helpers.integration_update_orchestrator import (
    IntegrationUpdateOrchestrator,
)


@pytest.fixture(scope="function", autouse=True)
def manage_registry_patching(request):
    """Conditionally patches getattr before each test and cleans up patch afterwards."""
    should_patch = not request.node.get_closest_marker("no_getattr_patch")
    if should_patch:
        registry_manager.patch_getattr()

    yield  # allows test function to run

    # Always restore getattr and clear per-test state
    registry_manager.cleanup_patch()


@pytest.fixture(scope="session", autouse=True)
def export_registry_data_at_end(request):
    """Processes and exports accumulated registry data at the end of the session."""
    yield  # allows test session to run

    # Process any modules that were patched and save the data for export
    registry_manager.process_patched_objects()
    if len(registry_manager.pending_updates) > 0:
        IntegrationUpdateOrchestrator.export_registry_data(registry_manager.pending_updates, request)
    # Always cleanup after session
    registry_manager.cleanup_post_session()


def pytest_sessionfinish(session):
    """Triggers the external registry update process and cleans up session data."""
    # integration registry data was stored in session.config._registry_session_data_file
    data_file_path = getattr(session.config, "_registry_session_data_file", None)

    if not data_file_path or not os.path.exists(data_file_path):
        IntegrationUpdateOrchestrator.cleanup_session_data(session)
        return

    project_root = str(session.config.rootdir)

    try:
        # run the integration registry update process
        orchestrator = IntegrationUpdateOrchestrator(project_root)
        orchestrator.run(data_file_path)
    except Exception as e:
        print(f"\nCritical error during registry update orchestration: {e}", file=sys.stderr)
    finally:
        IntegrationUpdateOrchestrator.cleanup_session_data(session)
