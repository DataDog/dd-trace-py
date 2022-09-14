from ddtrace.internal.logger import get_logger
from ddtrace.settings.dynamic_instrumentation import DynamicInstrumentationConfig


log = get_logger(__name__)

config = DynamicInstrumentationConfig()

log.debug("Dynamic instrumentation configuration: %r", config.__dict__)
