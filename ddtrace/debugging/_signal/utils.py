from collections import Counter
from collections import OrderedDict
from collections import defaultdict
from collections import deque
from collections.abc import Collection
from decimal import Decimal
from itertools import islice
from itertools import takewhile
from types import BuiltinFunctionType
from types import BuiltinMethodType
from types import FrameType
from types import FunctionType
from types import MethodType
from types import MethodWrapperType
from types import ModuleType
from types import TracebackType
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Optional

from wrapt.wrappers import BoundFunctionWrapper
from wrapt.wrappers import FunctionWrapper

from ddtrace.debugging._probe.model import MAXFIELDS
from ddtrace.debugging._probe.model import MAXLEN
from ddtrace.debugging._probe.model import MAXLEVEL
from ddtrace.debugging._probe.model import MAXSIZE
from ddtrace.debugging._redaction import REDACTED_PLACEHOLDER
from ddtrace.debugging._redaction import redact
from ddtrace.debugging._redaction import redact_type
from ddtrace.debugging._safety import get_fields
from ddtrace.debugging._safety import safe_getattr
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.safety import _isinstance


EXCLUDED_FIELDS = frozenset(["__class__", "__dict__", "__weakref__", "__doc__", "__module__", "__hash__"])

NoneType = type(None)

BUILTIN_SIMPLE_TYPES = frozenset([int, float, str, bytes, bool, NoneType, type, complex, Decimal])
BUILTIN_MAPPING_TYPES = frozenset([dict, defaultdict, Counter, OrderedDict])
BUILTIN_SEQUENCE_TYPES = frozenset([list, tuple, set, frozenset, deque])
BUILTIN_CONTAINER_TYPES = BUILTIN_MAPPING_TYPES | BUILTIN_SEQUENCE_TYPES
BUILTIN_TYPES = BUILTIN_SIMPLE_TYPES | BUILTIN_CONTAINER_TYPES


CALLABLE_TYPES = (
    BuiltinMethodType,
    BuiltinFunctionType,
    FunctionType,
    MethodType,
    MethodWrapperType,
    FunctionWrapper,
    BoundFunctionWrapper,
    property,
    classmethod,
    staticmethod,
)


SIMPLE_TYPES: frozenset[type] = BUILTIN_SIMPLE_TYPES
CONTAINER_TYPES: frozenset[type] = BUILTIN_CONTAINER_TYPES
# Types the serializer renders with list-style ("[]") brackets.
ARRAY_TYPES: frozenset[type] = frozenset((list, deque))

NUMPY_SIMPLE_TYPES: frozenset[type] = frozenset()
NDARRAY_TYPE: Optional[type] = None

_NUMPY_SCALAR_TYPE_NAMES = (
    "int8",
    "int16",
    "int32",
    "int64",
    "uint8",
    "uint16",
    "uint32",
    "uint64",
    "float16",
    "float32",
    "float64",
    "float96",
    "float128",
    # longdouble/clongdouble are always present, unlike the platform-dependent
    # float128/complex256 aliases above.
    "longdouble",
    "clongdouble",
    "complex64",
    "complex128",
    "complex192",
    "complex256",
)


@ModuleWatchdog.after_module_imported("numpy")
def _(numpy: ModuleType) -> None:
    global SIMPLE_TYPES, CONTAINER_TYPES, ARRAY_TYPES, NUMPY_SIMPLE_TYPES, NDARRAY_TYPE

    NUMPY_SIMPLE_TYPES = frozenset(getattr(numpy, n) for n in _NUMPY_SCALAR_TYPE_NAMES if hasattr(numpy, n))
    NDARRAY_TYPE = getattr(numpy, "ndarray", None)
    ndarray_set: frozenset[type] = frozenset() if NDARRAY_TYPE is None else frozenset((NDARRAY_TYPE,))

    SIMPLE_TYPES = BUILTIN_SIMPLE_TYPES | NUMPY_SIMPLE_TYPES
    CONTAINER_TYPES = BUILTIN_CONTAINER_TYPES | ndarray_set
    ARRAY_TYPES = frozenset((list, deque)) | ndarray_set


def _is_namedtuple_type(cls: type) -> bool:
    mro = safe_getattr(cls, "__mro__", None)
    if not (type(mro) is tuple and tuple in mro):
        return False

    fields = safe_getattr(cls, "_fields", None)
    return type(fields) is tuple and all(type(f) is str for f in fields)


def _fields_of(value: Any) -> dict[str, Any]:
    if _is_namedtuple_type(type(value)):
        return dict(zip(value._fields, value))

    return get_fields(value)


def _serialize_collection(
    value: Collection[Any], brackets: str, level: int, maxsize: int, maxlen: int, maxfields: int
) -> str:
    o, c = brackets[0], brackets[1]
    ellipsis = ", ..." if len(value) > maxsize else ""
    return "".join(
        (o, ", ".join(serialize(_, level - 1, maxsize, maxlen, maxfields) for _ in islice(value, maxsize)), ellipsis, c)
    )


def serialize(
    value: Any, level: int = MAXLEVEL, maxsize: int = MAXSIZE, maxlen: int = MAXLEN, maxfields: int = MAXFIELDS
) -> str:
    """Python object serializer.

    We provide our own serializer to avoid any potential side effects of calling
    ``str`` directly on arbitrary objects.
    """

    if _isinstance(value, CALLABLE_TYPES):
        return object.__repr__(value)

    if type(value) in SIMPLE_TYPES:
        r = repr(value)
        return "".join((r[:maxlen], "..." + ("'" if r[0] == "'" else "") if len(r) > maxlen else ""))

    if not level:
        return repr(type(value))

    if type(value) is NDARRAY_TYPE and value.ndim == 0:
        # A 0-dimensional array is a scalar; serialize its scalar item instead.
        return serialize(value[()], level, maxsize, maxlen, maxfields)

    if type(value) in BUILTIN_MAPPING_TYPES:
        return "{%s}" % ", ".join(
            (
                ": ".join(
                    (
                        serialize(_, level - 1, maxsize, maxlen, maxfields)
                        for _ in (k, v if not (_isinstance(k, (str, bytes)) and redact(k)) else REDACTED_PLACEHOLDER)
                    )
                )
                for k, v in islice(value.items(), maxsize)
            )
        )
    elif type(value) in ARRAY_TYPES:
        return _serialize_collection(value, "[]", level, maxsize, maxlen, maxfields)
    elif type(value) is tuple:
        return _serialize_collection(value, "()", level, maxsize, maxlen, maxfields)
    elif type(value) in {set, frozenset}:
        return _serialize_collection(value, r"{}", level, maxsize, maxlen, maxfields) if value else "set()"

    return "%s(%s)" % (
        type(value).__name__,
        ", ".join(
            (
                "=".join((k, serialize(v, level - 1, maxsize, maxlen, maxfields)))
                for k, v in islice(_fields_of(value).items(), maxfields)
                if not redact(k)
            )
        ),
    )


def capture_stack(top_frame: FrameType, max_height: int = 4096) -> list[dict[str, Any]]:
    frame: Optional[FrameType] = top_frame
    stack = []
    h = 0
    while frame and h < max_height:
        code = frame.f_code
        stack.append(
            {
                "fileName": code.co_filename,
                "function": code.co_name,
                "lineNumber": frame.f_lineno,
            }
        )
        frame = frame.f_back
        h += 1
    return stack


def capture_traceback(tb: TracebackType, max_height: int = 4096) -> list[dict[str, Any]]:
    stack = []
    h = 0
    _tb: Optional[TracebackType] = tb
    while _tb is not None and h < max_height:
        frame = _tb.tb_frame
        code = frame.f_code
        stack.append(
            {
                "fileName": code.co_filename,
                "function": code.co_name,
                "lineNumber": _tb.tb_lineno,
            }
        )
        _tb = _tb.tb_next
        h += 1
    return stack


def capture_exc_info(exc_info: ExcInfoType) -> Optional[dict[str, Any]]:
    _type, value, tb = exc_info
    if _type is None or value is None:
        return None

    return {
        "type": _type.__name__,
        "message": ", ".join([serialize(v) for v in value.args]),
        "stacktrace": capture_traceback(tb) if tb is not None else None,
    }


def redacted_value(v: Any) -> dict[str, Any]:
    return {"type": type(v).__qualname__, "notCapturedReason": "redactedIdent"}


def redacted_type(t: Any) -> dict[str, Any]:
    return {"type": t.__qualname__, "notCapturedReason": "redactedType"}


def capture_pairs(
    pairs: Iterable[tuple[str, Any]],
    level: int = MAXLEVEL,
    maxlen: int = MAXLEN,
    maxsize: int = MAXSIZE,
    maxfields: int = MAXFIELDS,
    stopping_cond: Optional[Callable[[Any], bool]] = None,
) -> dict[str, Any]:
    return {
        n: (capture_value(v, level, maxlen, maxsize, maxfields, stopping_cond) if not redact(n) else redacted_value(v))
        for n, v in pairs
    }


def capture_value(
    value: Any,
    level: int = MAXLEVEL,
    maxlen: int = MAXLEN,
    maxsize: int = MAXSIZE,
    maxfields: int = MAXFIELDS,
    stopping_cond: Optional[Callable[[Any], bool]] = None,
) -> dict[str, Any]:
    cond = stopping_cond if stopping_cond is not None else (lambda _: False)

    _type = type(value)

    if redact_type(_type.__qualname__):
        return redacted_type(_type)

    if _type in SIMPLE_TYPES:
        if _type is NoneType:
            return {"type": "NoneType", "isNull": True}

        if cond(value):
            return {
                "type": _type.__qualname__,
                "notCapturedReason": cond.__name__,
            }

        value_repr = serialize(value)
        value_repr_len = len(value_repr)
        return (
            {
                "type": _type.__qualname__,
                "value": value_repr,
            }
            if value_repr_len <= maxlen
            else {
                "type": _type.__qualname__,
                "value": value_repr[:maxlen],
                "truncated": True,
                "size": value_repr_len,
            }
        )

    if _type in CONTAINER_TYPES:
        if _type is NDARRAY_TYPE and value.ndim == 0:
            # A 0-dimensional array is a scalar; capture its scalar item instead.
            return capture_value(value[()], level, maxlen, maxsize, maxfields, stopping_cond)

        if level < 0:
            return {
                "type": _type.__qualname__,
                "notCapturedReason": "depth",
                "size": len(value),
            }

        if cond(value):
            return {
                "type": _type.__qualname__,
                "notCapturedReason": cond.__name__,
                "size": len(value),
            }

        collection: Optional[list[Any]] = None
        concurrent_modification = False
        if _type in BUILTIN_MAPPING_TYPES:
            size = len(value)
            # For small mappings, use dict.copy() which is atomic under the GIL.
            # For large ones, only snapshot up to maxsize to avoid materializing
            # the whole collection when most of it would be discarded anyway.
            items_snapshot: Any = value.copy().items() if size <= maxsize else islice(value.items(), maxsize)
            try:
                collection = [
                    (
                        capture_value(
                            k,
                            level=level - 1,
                            maxlen=maxlen,
                            maxsize=maxsize,
                            maxfields=maxfields,
                            stopping_cond=cond,
                        ),
                        capture_value(
                            v,
                            level=level - 1,
                            maxlen=maxlen,
                            maxsize=maxsize,
                            maxfields=maxfields,
                            stopping_cond=cond,
                        )
                        if not (_isinstance(k, (str, bytes)) and redact(k))
                        else redacted_value(v),
                    )
                    for k, v in takewhile(lambda _: not cond(_), items_snapshot)
                ]
            except RuntimeError:
                collection = []
                concurrent_modification = True
            data = {
                "type": _type.__qualname__,
                "entries": collection,
                "size": size,
            }

        else:
            size = len(value)
            # Immutable types (tuple, frozenset) are safe to iterate directly.
            # Mutable types use .copy() for an atomic GIL-protected snapshot on
            # the small path. For large collections only snapshot up to maxsize
            # to avoid materializing the whole collection when most of it would
            # be discarded anyway.
            if _type is NDARRAY_TYPE:
                # Slicing an ndarray returns a cheap view that shares the backing
                # buffer, so we avoid deep-copying potentially huge arrays just to
                # snapshot up to maxsize top-level elements.
                value_snapshot: Any = value[:maxsize]
            elif size <= maxsize:
                value_snapshot = value if _type in {tuple, frozenset} else value.copy()
            else:
                value_snapshot = islice(value, maxsize)
            try:
                collection = [
                    capture_value(
                        v,
                        level=level - 1,
                        maxlen=maxlen,
                        maxsize=maxsize,
                        maxfields=maxfields,
                        stopping_cond=cond,
                    )
                    for v in takewhile(lambda _: not cond(_), value_snapshot)
                ]
            except RuntimeError:
                collection = []
                concurrent_modification = True
            data = {
                "type": _type.__qualname__,
                "elements": collection,
                "size": size,
            }

        if concurrent_modification:
            data["notCapturedReason"] = "concurrentModification"
        elif len(collection) < min(maxsize, size):
            data["notCapturedReason"] = cond.__name__
        elif size > maxsize:
            data["notCapturedReason"] = "collectionSize"

        return data

    # Arbitrary object
    if level < 0:
        return {
            "type": _type.__qualname__,
            "notCapturedReason": "depth",
        }

    if cond(value):
        return {
            "type": _type.__qualname__,
            "notCapturedReason": cond.__name__,
        }

    fields = _fields_of(value)

    # Capture exception chain for exceptions
    if _isinstance(value, BaseException):
        for attr in ("args", "__cause__", "__context__", "__suppress_context__"):
            try:
                fields[attr] = object.__getattribute__(value, attr)
            except AttributeError:
                pass

    captured_fields = {
        n: (
            capture_value(v, level=level - 1, maxlen=maxlen, maxsize=maxsize, maxfields=maxfields, stopping_cond=cond)
            if not redact(n)
            else redacted_value(v)
        )
        for n, v in takewhile(lambda _: not cond(_), islice(fields.copy().items(), maxfields))
    }
    data = {
        "type": _type.__qualname__,
        "fields": captured_fields,
    }
    if len(captured_fields) < min(maxfields, len(fields)):
        data["notCapturedReason"] = cond.__name__
    elif len(fields) > maxfields:
        data["notCapturedReason"] = "fieldCount"

    if _isinstance(value, BaseException):
        # DEV: Celery doesn't like that we store references to these objects so we
        # delete them as soon as we're done with them.
        for attr in ("args", "__cause__", "__context__", "__suppress_context__"):
            if attr in fields:
                del fields[attr]

    return data
