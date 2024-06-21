is_available = False

try:
    from ._crashtracker import *  # noqa: F403, F401

    is_available = True

except Exception as e:
    from ddtrace.internal.logger import get_logger

    from ._crashtracker_stubs import *  # noqa: F403, F401

    LOG = get_logger(__name__)
    LOG.warning("Failed to import _crashtracker: %s", e)
