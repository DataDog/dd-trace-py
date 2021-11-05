# [Backward compatibility]: keep importing modules functions
from ..utils.deprecation import deprecation
from ..utils.importlib import func_name
from ..utils.importlib import module_name
from ..utils.importlib import require_modules


deprecation(
    name="ddtrace.contrib.util",
    message="`ddtrace.contrib.util` module will be removed in 1.0.0",
    version="1.0.0",
)

__all__ = [
    "require_modules",
    "func_name",
    "module_name",
]
