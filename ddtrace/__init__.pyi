import ddtrace
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning as DDTraceDeprecationWarning
from ddtrace.pin import Pin as Pin
import ddtrace.settings.config
from ddtrace.span import Span as Span
from ddtrace.tracer import Tracer as Tracer

def patch_all(**patch_modules: bool) -> None: ...
def patch(raise_errors: bool = ..., patch_modules_prefix: str = ..., **patch_modules: bool) -> None: ...

config: ddtrace.settings.config.Config
tracer: Tracer
__version__: str
