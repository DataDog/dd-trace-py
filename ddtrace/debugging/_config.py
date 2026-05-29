from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._registry import Config
from ddtrace.internal.settings.exception_replay import config as er_config


di_config = Config.get().dynamic_instrumentation


__all__ = ["di_config", "er_config"]

log = get_logger(__name__)
