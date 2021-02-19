# [Backward compatibility]: keep importing modules functions
from ..utils.deprecation import deprecation
from ..utils.importlib import func_name
from ..utils.importlib import module_name
from ..utils.importlib import require_modules


deprecation(
    name='ddtrace.contrib.util',
    message='Use `ddtrace.utils.importlib` module instead',
    version='1.0.0',
)

__all__ = [
    'require_modules',
    'func_name',
    'module_name',
]
