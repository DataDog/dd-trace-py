import os
import sys

import pytest

from tests.contrib.integration_registry.registry_update_helpers.integration_registry_manager import registry_manager
from tests.contrib.integration_registry.registry_update_helpers.integration_update_orchestrator import (
    IntegrationUpdateOrchestrator,
)


@pytest.fixture(scope="session", autouse=True)
def process_patches_at_end(request):
    """Manages registry data collection/export and manager cleanup."""
    registry_manager.patch_getattr()

    yield  # allows test session to run

    # Process any modules that were patched and save the data for export
    registry_manager.process_patched_objects()
    if len(registry_manager.pending_updates) > 0:
        IntegrationUpdateOrchestrator.export_registry_data(registry_manager.pending_updates, request)
    registry_manager.cleanup()


def pytest_sessionfinish(session):
    """Triggers the external registry update process and cleans up session data."""
    # integration registry data was stored in session.config._registry_session_data_file
    data_file_path = getattr(session.config, "_registry_session_data_file", None)

    if not data_file_path or not os.path.exists(data_file_path):
        print(f"\nWarning: Integration Registry data file missing ({data_file_path}). Skipping registry update.", file=sys.stderr)
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
