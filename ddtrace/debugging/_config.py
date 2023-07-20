from ddtrace.internal.logger import get_logger
from ddtrace.settings.dynamic_instrumentation import config as di_config  # noqa: F401
from ddtrace.settings.exception_debugging import config as ed_config  # noqa: F401


log = get_logger(__name__)
