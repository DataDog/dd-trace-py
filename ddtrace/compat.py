from ddtrace.internal.utils.deprecation import deprecation

from .internal.compat import CONTEXTVARS_IS_AVAILABLE
from .internal.compat import NumericType
from .internal.compat import PY2
from .internal.compat import PY3
from .internal.compat import PYTHON_INTERPRETER
from .internal.compat import PYTHON_VERSION
from .internal.compat import PYTHON_VERSION_INFO
from .internal.compat import Queue
from .internal.compat import StringIO
from .internal.compat import binary_type
from .internal.compat import contextvars
from .internal.compat import ensure_text
from .internal.compat import get_connection_response
from .internal.compat import getrandbits
from .internal.compat import httplib
from .internal.compat import is_integer
from .internal.compat import iscoroutinefunction
from .internal.compat import iteritems
from .internal.compat import main_thread
from .internal.compat import make_async_decorator
from .internal.compat import monotonic
from .internal.compat import monotonic_ns
from .internal.compat import msgpack_type
from .internal.compat import numeric_types
from .internal.compat import parse
from .internal.compat import pattern_type
from .internal.compat import process_time_ns
from .internal.compat import reload_module
from .internal.compat import reraise
from .internal.compat import string_type
from .internal.compat import stringify
from .internal.compat import time_ns
from .internal.compat import to_unicode
from .internal.compat import urlencode


__all__ = [
    "CONTEXTVARS_IS_AVAILABLE",
    "NumericType",
    "PY2",
    "PY3",
    "PYTHON_INTERPRETER",
    "PYTHON_VERSION",
    "PYTHON_VERSION_INFO",
    "Queue",
    "StringIO",
    "binary_type",
    "contextvars",
    "ensure_text",
    "get_connection_response",
    "getrandbits",
    "httplib",
    "is_integer",
    "iscoroutinefunction",
    "iteritems",
    "main_thread",
    "make_async_decorator",
    "monotonic",
    "monotonic_ns",
    "msgpack_type",
    "numeric_types",
    "parse",
    "pattern_type",
    "process_time_ns",
    "reload_module",
    "reraise",
    "string_type",
    "stringify",
    "time_ns",
    "to_unicode",
    "urlencode",
]

deprecation(
    name="ddtrace.compat",
    message="The compat module has been moved to ddtrace.internal and will no longer be part of the public API.",
    version="1.0.0",
)
