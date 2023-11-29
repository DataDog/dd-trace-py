from ddtrace import config as ddconfig
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def skip_if_agentless(func):
    """Deocrator to skip sending telemetry if we are in agentless mode as it is not currently supported."""

    def wrapper(*args, **kwargs):
        if ddconfig._ci_visibility_agentless_enabled:
            log.debug("Running in agentless mode, skipping sending telemetry")
            return
        func(*args, **kwargs)

    return wrapper
