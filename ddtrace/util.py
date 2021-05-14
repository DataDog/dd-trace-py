# [Backward compatibility]: keep importing modules functions
from .utils.deprecation import deprecated
from .utils.deprecation import deprecation
from .utils.formats import asbool
from .utils.formats import deep_getattr
from .utils.formats import get_env
from .utils.wrappers import safe_patch
from .utils.wrappers import unwrap


deprecation(
    name="ddtrace.util",
    message="Use `ddtrace.utils` package instead",
    version="1.0.0",
)

__all__ = [
    "deprecated",
    "asbool",
    "deep_getattr",
    "get_env",
    "safe_patch",
    "unwrap",
]
