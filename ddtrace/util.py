# [Backward compatibility]: keep importing modules functions
from .internal.utils.deprecation import deprecated
from .internal.utils.deprecation import deprecation
from .internal.utils.formats import asbool
from .internal.utils.formats import deep_getattr
from .internal.utils.formats import get_env
from .internal.utils.wrappers import safe_patch
from .internal.utils.wrappers import unwrap


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
