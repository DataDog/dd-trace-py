from ddtrace.internal.logger import get_logger
from ddtrace.settings.debugger import DebuggerConfig


log = get_logger(__name__)

config = DebuggerConfig()

log.debug("Debugger configuration: %r", config.__dict__)
