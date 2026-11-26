"""Where dd-trace-py's code is, so telemetry can tell which unhandled exceptions it caused.

Telemetry lives in a package that knows nothing about where dd-trace-py keeps its code. It loads this module through
the ``ddtrace_internal.library`` entry point in ``pyproject.toml``, only when an error needs attributing (see
``ddtrace.internal.telemetry.inputs``).
"""

import os
from typing import Optional


# An exception is blamed on the tracer when it is raised in the `ddtrace` package or in a `_ddtrace_<package>` root,
# the packages that hold code moved out of it.
code_roots = ("ddtrace/", "/_ddtrace_")


def integration_of(filename: str) -> Optional[str]:
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
