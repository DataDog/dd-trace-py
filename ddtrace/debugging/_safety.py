from inspect import CO_VARARGS
from inspect import CO_VARKEYWORDS
from itertools import chain
from types import FrameType
from types import MappingProxyType
from typing import Any
from typing import Iterator
from typing import Optional

from ddtrace.internal.safety import get_slots


GetSetDescriptor = type(type.__dict__["__dict__"])  # type: ignore[index]  # noqa: F821

# Mappings whose __getitem__ is implemented in C and therefore cannot run user
# code. A class __dict__ is always a mappingproxy; a plain instance __dict__ is
# a dict.
SAFE_MAPPING_TYPES = frozenset({dict, MappingProxyType})

# Direct handle on type's own __qualname__ getset_descriptor.
_type_qualname_descriptor: Any = type.__dict__["__qualname__"]  # type: ignore[index]


def safe_qualname(cls: type) -> str:
    return _type_qualname_descriptor.__get__(cls)  # type: ignore[no-any-return]


def get_args(frame: FrameType) -> Iterator[tuple[str, Any]]:
    code = frame.f_code
    _locals = frame.f_locals
    # co_varnames lays out arguments as: positional, then keyword-only,
    # then *args (if CO_VARARGS), then **kwargs (if CO_VARKEYWORDS).
    # Without co_kwonlyargcount in the offset, keyword-only args were
    # captured as positional and *args / **kwargs were missed entirely
    # (and then leaked into get_locals as if they were function-local
    # variables).
    nargs = (
        code.co_argcount
        + code.co_kwonlyargcount
        + bool(code.co_flags & CO_VARARGS)
        + bool(code.co_flags & CO_VARKEYWORDS)
    )
    arg_names = code.co_varnames[:nargs]
    return ((name, _locals.get(name)) for name in arg_names)


def get_locals(frame: FrameType) -> Iterator[tuple[str, Any]]:
    code = frame.f_code
    _locals = frame.f_locals
    nargs = (
        code.co_argcount
        + code.co_kwonlyargcount
        + bool(code.co_flags & CO_VARARGS)
        + bool(code.co_flags & CO_VARKEYWORDS)
    )
    return (
        (name, _locals.get(name)) for name in chain(code.co_varnames[nargs:], code.co_freevars, code.co_cellvars)
    )  # include freevars and cellvars


def get_globals(frame: FrameType) -> Iterator[tuple[str, Any]]:
    """Get global variables referenced by the frame's code object."""
    nonlocal_names = frame.f_code.co_names
    _globals = frame.f_globals

    return ((name, _globals[name]) for name in nonlocal_names if name in _globals)


def getattr_or_exception(obj: Any, name: str) -> Any:
    try:
        return object.__getattribute__(obj, name)
    except Exception as e:
        return e


def safe_getattr(obj: Any, name: str, default: Optional[Any] = None) -> Optional[Any]:
    try:
        return object.__getattribute__(obj, name)
    except AttributeError:
        return default


def safe_get_type_attr(cls: type, name: str, default: Optional[Any] = None) -> Optional[Any]:
    # Static attribute lookup over a class's MRO. Each class's own __dict__ is
    # read directly rather than going through getattr, so a descriptor (or a
    # metaclass __getattr__) is never invoked and no user code ever runs.

    mro = safe_getattr(cls, "__mro__", None)
    if type(mro) is not tuple:
        return default

    for base in mro:
        base_dict = safe_getattr(base, "__dict__", None)
        if type(base_dict) in SAFE_MAPPING_TYPES:
            try:
                return base_dict[name]  # type: ignore[index]
            except KeyError:
                continue

    return default


def safe_getitem(obj: Any, index: Any) -> Any:
    if isinstance(obj, list):
        return list.__getitem__(obj, index)
    elif isinstance(obj, dict):
        return dict.__getitem__(obj, index)
    elif isinstance(obj, tuple):
        return tuple.__getitem__(obj, index)
    raise TypeError("Type is not indexable collection " + str(type(obj)))


def _safe_dict(o: Any) -> dict[str, Any]:
    try:
        if type(__dict__ := object.__getattribute__(o, "__dict__")) is dict:
            return __dict__.copy()
    except Exception:
        pass  # nosec

    raise AttributeError("No safe __dict__")


def get_namedtuple_fields(obj: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    # Read items positionally via the tuple base type instead of iterating
    # obj directly (i.e. instead of zip(fields, obj)): some namedtuple-like
    # classes are genuine tuple subclasses but override __iter__ (and other
    # tuple dunders) to ban tuple-style access.
    return dict(zip(fields, (tuple.__getitem__(obj, i) for i in range(tuple.__len__(obj)))))


def get_fields(obj: Any) -> dict[str, Any]:
    try:
        return _safe_dict(obj)
    except AttributeError:
        # Check for slots
        return {s: getattr_or_exception(obj, s) for s in get_slots(obj)}
