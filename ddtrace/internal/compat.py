import ipaddress
import sys
from types import TracebackType
from typing import Any
from typing import Optional  # noqa:F401
from typing import Text  # noqa:F401
from typing import Union  # noqa:F401

import wrapt


__all__ = [
    "maybe_stringify",
    "MAX_PY",
    "NEXT_MAX_PY",
    "NEXT_PY_UNSUPPORTED_MSG",
    "PYTHON_VERSION_INFO",
    "is_at_least_py",
    "is_at_most_py",
]

PYTHON_VERSION_INFO = sys.version_info

# Last officially supported CPython. Matches requires-python <3.15.
# TODO(py-315): bump MAX_PY to (3, 15) after 3.15 GAs
MAX_PY: tuple[int, int] = (3, 14)

# Next CPython: packaging exclusive ceiling and wrap-live inclusive ceiling.
# wrap() is live through NEXT_MAX_PY (`is_at_most_py(*NEXT_MAX_PY)`); 3.16 dies.
# TODO(py-315): bump NEXT_MAX_PY to (3, 16) after 3.15 GAs
NEXT_MAX_PY: tuple[int, int] = (3, 15)

NEXT_PY_UNSUPPORTED_MSG: str = "This version of CPython is not supported yet (Python %s.%s and later)" % (
    NEXT_MAX_PY[0],
    NEXT_MAX_PY[1] + 1,
)


def is_at_least_py(major: int, minor: int, version: Optional[tuple[int, ...]] = None) -> bool:
    """True if version is at or past (major, minor).

    Feature gates pass literals (`is_at_least_py(3, 15)`), not NEXT_MAX_PY.
    The rolling next-max floor is `is_at_least_py(*NEXT_MAX_PY)`.
    """
    version = version or PYTHON_VERSION_INFO[:2]
    return version[:2] >= (major, minor)


def is_at_most_py(major: int, minor: int, version: Optional[tuple[int, ...]] = None) -> bool:
    """True if version is at or below (major, minor) inclusive.

    Exclusive `< (3, 13)` is `is_at_most_py(3, 12)`, not `is_at_most_py(3, 13)`.
    Official support is `is_at_most_py(*MAX_PY)`. Wrap is live through
    `is_at_most_py(*NEXT_MAX_PY)`. Feature gates stay `is_at_least_py(3, 15)`
    literals, not these constants.
    """
    version = version or PYTHON_VERSION_INFO[:2]
    return version[:2] <= (major, minor)


def ensure_text(s, encoding="utf-8", errors="ignore") -> str:
    if isinstance(s, str):
        return s
    if isinstance(s, bytes):
        return s.decode(encoding, errors)
    raise TypeError("Expected str or bytes but received %r" % (s.__class__))


def ensure_binary(s, encoding="utf-8", errors="ignore") -> bytes:
    if isinstance(s, bytes):
        return s
    if not isinstance(s, str):
        raise TypeError("Expected str or bytes but received %r" % (s.__class__))
    return s.encode(encoding, errors)


NumericType = Union[int, float]


def is_integer(obj: Any) -> bool:
    """Helper to determine if the provided ``obj`` is an integer type or not"""
    # DEV: We have to make sure it is an integer and not a boolean
    # >>> type(True)
    # <class 'bool'>
    # >>> isinstance(True, int)
    # True
    return isinstance(obj, int) and not isinstance(obj, bool)


def maybe_stringify(obj: Any) -> Optional[str]:
    if obj is not None:
        return str(obj)
    return None


ExcInfoType = Union[tuple[type[BaseException], BaseException, Optional[TracebackType]], tuple[None, None, None]]

# Sentinel value to represent "no exception"
NO_EXCEPTION: ExcInfoType = (None, None, None)


def is_valid_ip(ip: str) -> bool:
    try:
        # try parsing the IP address
        ipaddress.ip_address(str(ip))
        return True
    except Exception:
        return False


def ip_is_global(ip: str) -> bool:
    """
    is_global is Python 3+ only. This could raise a ValueError if the IP is not valid.
    """
    parsed_ip = ipaddress.ip_address(str(ip))

    return parsed_ip.is_global


# This fix was implemented in 3.9.8
# https://github.com/python/cpython/issues/83860
if PYTHON_VERSION_INFO >= (3, 9, 8):
    from functools import singledispatchmethod
else:
    from functools import singledispatchmethod

    def _register(self, cls, method=None):
        if hasattr(cls, "__func__"):
            setattr(cls, "__annotations__", cls.__func__.__annotations__)
        return self.dispatcher.register(cls, func=method)

    singledispatchmethod.register = _register  # type: ignore[method-assign]


def get_mp_context():
    import multiprocessing

    return multiprocessing.get_context("fork" if sys.platform != "win32" else "spawn")


def __getattr__(name: str) -> Any:
    # These attributes are expensive to pre-compute, so we make them lazy
    if name == "PYTHON_VERSION":
        from platform import python_version

        globals()[name] = python_version()

    elif name == "PYTHON_INTERPRETER":
        from platform import python_implementation

        globals()[name] = python_implementation()

    try:
        return globals()[name]
    except KeyError:
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


if hasattr(wrapt, "BaseObjectProxy"):
    # This must be used for wrapt version >= 2.0.0
    wrapt_class: type = wrapt.BaseObjectProxy
else:
    wrapt_class = wrapt.ObjectProxy


def is_wrapted(o: object) -> bool:
    return isinstance(o, wrapt_class)
