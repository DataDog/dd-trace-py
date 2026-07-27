# DEPRECATED: This module is scheduled for removal in dd-trace-py 5.0.0.
# Use DD_PYTEST_USE_NEW_PLUGIN=true (or unset; it is now the default) to opt into
# the new plugin at ddtrace/testing/internal/pytest/.
# Get version is imported from patch.py in _monkey.py
def get_version() -> str:
    import pytest

    return pytest.__version__


def _supported_versions() -> dict[str, str]:
    return {"pytest": ">=6.0"}
