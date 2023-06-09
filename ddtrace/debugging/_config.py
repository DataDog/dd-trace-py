from ddtrace.internal.logger import get_logger
from ddtrace.settings.dynamic_instrumentation import DynamicInstrumentationConfig
from ddtrace.settings.exception_debugging import ExceptionDebuggingConfig


log = get_logger(__name__)

di_config = DynamicInstrumentationConfig()
ed_config = ExceptionDebuggingConfig()
