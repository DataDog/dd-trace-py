# See ddtrace/internal/datadog/profiling/crashtracker/_crashtracker.pyx or .pyi for a summary of
# the available interfaces

import platform


is_available = False

# Only try importing on Linux
if platform.system() == "Linux":
    try:
        from ..datadog.profiling.crashtracker._crashtracker import *  # noqa: F403, F401

        is_available = True

    except Exception as e:
        from ddtrace.internal.logger import get_logger

        LOG = get_logger(__name__)
        LOG.warning("Failed to import _crashtracker: %s", e)
