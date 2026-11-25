"""Which integration an unhandled exception came from, by the file that raised it.

Telemetry reports integration errors, but lives in a package that knows nothing about where dd-trace-py keeps its
integrations. ``ddtrace/__init__.py`` registers this with the library telemetry reports for.
"""

import os
from typing import Optional


def integration_from_filename(filename: str) -> Optional[str]:
    """Return the integration for a file in ``ddtrace/contrib/<name>/`` or ``ddtrace/contrib/internal/<name>/``."""
    dir_parts = filename.split(os.path.sep)
    if "ddtrace" not in dir_parts or "contrib" not in dir_parts:
        return None

    ddtrace_index = dir_parts.index("ddtrace")
    contrib_index = dir_parts.index("contrib")
    # Check if the filename has the following format:
    # `../ddtrace/contrib/integration_name/..(subpath and/or file)...`
    if ddtrace_index + 1 != contrib_index or len(dir_parts) - 2 <= contrib_index:
        return None

    integration_name = dir_parts[contrib_index + 1]
    if "internal" in dir_parts:
        # Check if the filename has the format:
        # `../ddtrace/contrib/internal/integration_name/..(subpath and/or file)...`
        internal_index = dir_parts.index("internal")
        integration_name = dir_parts[internal_index + 1]
    return integration_name
