import ipaddress
import sys
from types import TracebackType
from typing import Any
from typing import Optional  # noqa:F401
from typing import Text  # noqa:F401
from typing import Tuple  # noqa:F401
from typing import Type  # noqa:F401
from typing import Union  # noqa:F401


__all__ = [
    "maybe_stringify",
]

PYTHON_VERSION_INFO = sys.version_info


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


ExcInfoType = Union[Tuple[Type[BaseException], BaseException, Optional[TracebackType]], Tuple[None, None, None]]


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


if PYTHON_VERSION_INFO >= (3, 9, 8):
    from functools import singledispatchmethod
else:
    # This fix was not backported to 3.8
    # https://github.com/python/cpython/issues/83860
    from functools import singledispatchmethod

    def _register(self, cls, method=None):
        if hasattr(cls, "__func__"):
            setattr(cls, "__annotations__", cls.__func__.__annotations__)
        return self.dispatcher.register(cls, func=method)

    singledispatchmethod.register = _register  # type: ignore[method-assign]


if PYTHON_VERSION_INFO >= (3, 9):
    from pathlib import Path
else:
    from pathlib import Path

    # Taken from Python 3.9. This is not implemented in older versions of Python
    def is_relative_to(self, other):
        """Return True if the path is relative to another path or False."""
        try:
            self.relative_to(other)
            return True
        except ValueError:
            return False

    Path.is_relative_to = is_relative_to  # type: ignore[assignment]


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
