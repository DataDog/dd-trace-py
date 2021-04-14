from ddtrace.utils.deprecation import deprecation

from .internal.compat import CONTEXTVARS_IS_AVAILABLE  # noqa: F401
from .internal.compat import NumericType  # noqa: F401
from .internal.compat import PY2
from .internal.compat import PY3  # noqa: F401
from .internal.compat import PYTHON_INTERPRETER  # noqa: F401
from .internal.compat import PYTHON_VERSION  # noqa: F401
from .internal.compat import PYTHON_VERSION_INFO  # noqa: F401
from .internal.compat import Queue
from .internal.compat import StringIO
from .internal.compat import binary_type  # noqa: F401
from .internal.compat import contextvars  # noqa: F401
from .internal.compat import ensure_text  # noqa: F401
from .internal.compat import get_connection_response  # noqa: F401
from .internal.compat import getrandbits  # noqa: F401
from .internal.compat import httplib
from .internal.compat import is_integer  # noqa: F401
from .internal.compat import iscoroutinefunction  # noqa: F401
from .internal.compat import iteritems
from .internal.compat import main_thread  # noqa: F401
from .internal.compat import make_async_decorator  # noqa: F401
from .internal.compat import monotonic  # noqa: F401
from .internal.compat import monotonic_ns  # noqa: F401
from .internal.compat import msgpack_type  # noqa: F401
from .internal.compat import numeric_types  # noqa: F401
from .internal.compat import parse
from .internal.compat import pattern_type  # noqa: F401
from .internal.compat import process_time_ns  # noqa: F401
from .internal.compat import reload_module  # noqa: F401
from .internal.compat import reraise
from .internal.compat import string_type  # noqa: F401
from .internal.compat import stringify
from .internal.compat import time_ns  # noqa: F401
from .internal.compat import to_unicode  # noqa: F401
from .internal.compat import urlencode


__all__ = [
    "httplib",
    "iteritems",
    "PY2",
    "Queue",
    "stringify",
    "StringIO",
    "urlencode",
    "parse",
    "reraise",
]

deprecation(
    name="ddtrace.compat",
    message="The compat module has been moved to ddtrace.internal and will no longer be part of the public API.",
    version="1.0.0",
)
