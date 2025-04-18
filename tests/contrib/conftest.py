import os
import sys

import pytest

from tests.contrib.integration_registry.registry_update_helpers.integration_registry_manager import registry_manager
from tests.contrib.integration_registry.registry_update_helpers.update_orchestrator import cleanup_session_data
from tests.contrib.integration_registry.registry_update_helpers.update_orchestrator import export_registry_data
from tests.contrib.integration_registry.registry_update_helpers.update_orchestrator import run_update_process


@pytest.fixture(scope="session", autouse=True)
def process_patches_at_end(request):
    """Manages registry data collection/export and manager cleanup."""
    registry_manager.patch_getattr()

    yield  # allows test session to run

    # Process any modules that were patched and save the data for export
    registry_manager.process_patched_objects()
    data = registry_manager.get_processed_data_for_export()
    export_registry_data(data, request)
    registry_manager.cleanup()


def pytest_sessionfinish(session):
    """Triggers the external registry update process and cleans up session data."""
    # integration registry data was stored in session.config._registry_session_data_file
    data_file_path = getattr(session.config, "_registry_session_data_file", None)

    if not data_file_path or not os.path.exists(data_file_path):
        print(f"Warning: Registry data file missing ({data_file_path}). Skipping update.", file=sys.stderr)
        cleanup_session_data(session)
        return

    project_root = str(session.config.rootdir)

    try:
        # run the integration registry update process
        run_update_process(project_root, data_file_path)
    finally:
        cleanup_session_data(session)
