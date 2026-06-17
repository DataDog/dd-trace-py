from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.dynamic_instrumentation import config as di_config
from ddtrace.internal.settings.exception_replay import config as er_config


__all__ = ["di_config", "er_config"]

log = get_logger(__name__)
