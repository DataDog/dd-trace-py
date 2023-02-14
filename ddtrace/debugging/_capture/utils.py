from itertools import islice
from types import FrameType
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import TYPE_CHECKING
from typing import Type

from ddtrace.debugging._probe.model import MAXFIELDS
from ddtrace.debugging._probe.model import MAXLEN
from ddtrace.debugging._probe.model import MAXLEVEL
from ddtrace.debugging._probe.model import MAXSIZE
from ddtrace.debugging.safety import get_fields
from ddtrace.internal.compat import BUILTIN_CONTAINER_TYPES
from ddtrace.internal.compat import BUILTIN_SIMPLE_TYPES
from ddtrace.internal.compat import CALLABLE_TYPES
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.compat import NoneType
from ddtrace.internal.compat import stringify
from ddtrace.internal.safety import _isinstance
from ddtrace.internal.utils.cache import cached


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace.internal.compat import Collection

EXCLUDED_FIELDS = frozenset(["__class__", "__dict__", "__weakref__", "__doc__", "__module__", "__hash__"])


@cached()
def qualname(_type):
    # type: (Type) -> str
    try:
        return stringify(_type.__qualname__)
    except AttributeError:
        # The logic for implementing qualname in Python 2 is complex, so if we
        # don't have it, we just return the name of the type.
        try:
            return _type.__name__
        except AttributeError:
            return repr(_type)


def _serialize_collection(value, brackets, level, maxsize, maxlen, maxfields):
    # type: (Collection, str, int, int, int, int) -> str
    o, c = brackets[0], brackets[1]
    ellipsis = ", ..." if len(value) > maxsize else ""
    return "".join(
        (o, ", ".join(serialize(_, level - 1, maxsize, maxlen, maxfields) for _ in islice(value, maxsize)), ellipsis, c)
    )


def serialize(value, level=MAXLEVEL, maxsize=MAXSIZE, maxlen=MAXLEN, maxfields=MAXFIELDS):
    # type: (Any, int, int, int, int) -> str
    """Python object serializer.

    We provide our own serializer to avoid any potential side effects of calling
    ``str`` directly on arbitrary objects.
    """

    if _isinstance(value, CALLABLE_TYPES):
        return object.__repr__(value)

    if type(value) in BUILTIN_SIMPLE_TYPES:
        r = repr(value)
        return "".join((r[:maxlen], "..." + ("'" if r[0] == "'" else "") if len(r) > maxlen else ""))

    if not level:
        return repr(type(value))

    if type(value) not in BUILTIN_CONTAINER_TYPES:
        return (
            type(value).__name__
            + "("
            + ", ".join(
                [
                    "=".join((k, serialize(v, level - 1, maxsize, maxlen, maxfields)))
                    for k, v in list(get_fields(value).items())[:maxfields]
                ]
            )
            + ")"
        )

    if type(value) is dict:
        return (
            "{"
            + ", ".join(
                [
                    ": ".join((serialize(k, level - 1, maxsize, maxlen, maxfields), serialize(v, level - 1)))
                    for k, v in value.items()
                ]
            )
            + "}"
        )
    elif type(value) is list:
        return _serialize_collection(value, "[]", level, maxsize, maxlen, maxfields)
    elif type(value) is tuple:
        return _serialize_collection(value, "()", level, maxsize, maxlen, maxfields)
    elif type(value) is set:
        return _serialize_collection(value, r"{}", level, maxsize, maxlen, maxfields) if value else "set()"

    raise TypeError("Unhandled type: %s", type(value))


def capture_stack(top_frame, max_height=4096):
    # type: (FrameType, int) -> List[dict]
    frame = top_frame  # type: Optional[FrameType]
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


def capture_exc_info(exc_info):
    # type: (ExcInfoType) -> Optional[Dict[str, Any]]
    _type, value, tb = exc_info
    if _type is None or value is None:
        return None

    top_tb = tb
    if top_tb is not None:
        while top_tb.tb_next is not None:
            top_tb = top_tb.tb_next

    return {
        "type": _type.__name__,
        "message": ", ".join([serialize(v) for v in value.args]),
        "stacktrace": capture_stack(top_tb.tb_frame) if top_tb is not None else None,
    }


def capture_value(value, level=MAXLEVEL, maxlen=MAXLEN, maxsize=MAXSIZE, maxfields=MAXFIELDS):
    # type: (Any, int, int, int, int) -> Dict[str, Any]
    _type = type(value)

    if _type in BUILTIN_SIMPLE_TYPES:
        if _type is NoneType:
            return {"type": "NoneType", "isNull": True}

        value_repr = repr(value)
        value_repr_len = len(value_repr)
        return (
            {
                "type": qualname(_type),
                "value": value_repr,
            }
            if value_repr_len <= maxlen
            else {
                "type": qualname(_type),
                "value": value_repr[:maxlen],
                "truncated": True,
                "size": value_repr_len,
            }
        )

    if _type in BUILTIN_CONTAINER_TYPES:
        if level < 0:
            return {
                "type": qualname(_type),
                "notCapturedReason": "depth",
                "size": len(value),
            }

        if _type is dict:
            # Mapping
            data = {
                "type": "dict",
                "entries": [
                    (
                        capture_value(k, level=level - 1, maxlen=maxlen, maxsize=maxsize, maxfields=maxfields),
                        capture_value(v, level=level - 1, maxlen=maxlen, maxsize=maxsize, maxfields=maxfields),
                    )
                    for _, (k, v) in zip(range(maxsize), value.items())
                ],
                "size": len(value),
            }

        else:
            # Sequence
            data = {
                "type": qualname(_type),
                "elements": [
                    capture_value(v, level=level - 1, maxlen=maxlen, maxsize=maxsize, maxfields=maxfields)
                    for _, v in zip(range(maxsize), value)
                ],
                "size": len(value),
            }

        if len(value) > maxsize:
            data["notCapturedReason"] = "collectionSize"

        return data

    # Arbitrary object
    if level < 0:
        return {
            "type": qualname(_type),
            "notCapturedReason": "depth",
        }

    fields = get_fields(value)
    data = {
        "type": qualname(_type),
        "fields": {
            n: capture_value(v, level=level - 1, maxlen=maxlen, maxsize=maxsize, maxfields=maxfields)
            for _, (n, v) in zip(range(maxfields), fields.items())
        },
    }

    if len(fields) > maxfields:
        data["notCapturedReason"] = "fieldCount"

    return data
