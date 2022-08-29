import abc
from inspect import CO_VARARGS
from inspect import CO_VARKEYWORDS
from itertools import islice
import json
import os
import sys
from time import time
from types import FrameType
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import TYPE_CHECKING
from typing import Tuple
from typing import Type
from typing import Union
from uuid import uuid4

import six

from ddtrace.debugging._config import config
from ddtrace.debugging._probe.model import FunctionProbe
from ddtrace.debugging._probe.model import LineProbe
from ddtrace.debugging._snapshot.model import Snapshot
from ddtrace.internal import forksafe
from ddtrace.internal._encoding import BufferFull
from ddtrace.internal.compat import BUILTIN_CONTAINER_TYPES
from ddtrace.internal.compat import BUILTIN_SIMPLE_TYPES
from ddtrace.internal.compat import BUILTIN_TYPES
from ddtrace.internal.compat import CALLABLE_TYPES
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.compat import stringify
from ddtrace.internal.logger import get_logger
from ddtrace.internal.safety import _isinstance
from ddtrace.internal.safety import get_slots
from ddtrace.internal.utils.cache import cached


if TYPE_CHECKING:
    from ddtrace.internal.compat import Collection


GetSetDescriptor = type(type.__dict__["__dict__"])  # type: ignore[index]

EXCLUDED_FIELDS = frozenset(["__class__", "__dict__", "__weakref__", "__doc__", "__module__", "__hash__"])

log = get_logger(__name__)


class JsonBuffer(object):
    def __init__(self, max_size=None):
        self.max_size = max_size
        self._reset()

    def put(self, item):
        # type: (bytes) -> int
        if self._flushed:
            self._reset()

        size = len(item)
        if self.size + size > self.max_size:
            raise BufferFull(self.size, size)

        if self.size > 2:
            self.size += 1
            self._buffer += b","
        self._buffer += item
        self.size += size
        return size

    def _reset(self):
        self.size = 2
        self._buffer = bytearray(b"[")
        self._flushed = False

    def flush(self):
        self._buffer += b"]"
        try:
            return self._buffer
        finally:
            self._flushed = True


class Encoder(six.with_metaclass(abc.ABCMeta)):
    @abc.abstractmethod
    def encode(self, item):
        # type: (Any) -> bytes
        """Encode the given snapshot."""


class SnapshotEncoder(Encoder):
    @abc.abstractmethod
    def encode(self, snapshot):
        # type: (Snapshot) -> bytes
        """Encode the given snapshot."""

    @abc.abstractmethod
    def capture_context(
        cls,
        arguments,  # type: List[Tuple[str, Any]]
        _locals,  # type: List[Tuple[str, Any]]
        throwable,  # type: ExcInfoType
        level=1,  # type: int
    ):
        # type: (...) -> Dict[str, Any]
        """Capture context on the spot."""


class BufferedEncoder(six.with_metaclass(abc.ABCMeta)):
    count = 0

    @abc.abstractmethod
    def put(self, item):
        # type: (Any) -> int
        """Enqueue the given item and returns its encoded size."""

    @abc.abstractmethod
    def encode(self):
        # type: () -> Optional[bytes]
        """Encode the given item."""


def _unwind_stack(top_frame, max_height=4096):
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


def _get_args(frame):
    # type: (FrameType) -> Iterator[Tuple[str, Any]]
    code = frame.f_code
    nargs = code.co_argcount + bool(code.co_flags & CO_VARARGS) + bool(code.co_flags & CO_VARKEYWORDS)
    arg_names = code.co_varnames[:nargs]
    arg_values = (frame.f_locals[name] for name in arg_names)

    return zip(arg_names, arg_values)


def _get_locals(frame):
    # type: (FrameType) -> Iterator[Tuple[str, Any]]
    code = frame.f_code
    nargs = code.co_argcount + bool(code.co_flags & CO_VARARGS) + bool(code.co_flags & CO_VARKEYWORDS)
    names = code.co_varnames[nargs:]
    values = (frame.f_locals.get(name) for name in names)

    return zip(names, values)


def _get_globals(frame):
    # type: (FrameType) -> Iterator[Tuple[str, Any]]
    nonlocal_names = frame.f_code.co_names
    _globals = globals()

    return ((name, _globals[name]) for name in nonlocal_names if name in _globals)


def _safe_getattr(obj, name):
    # type: (Any, str) -> Any
    try:
        return object.__getattribute__(obj, name)
    except Exception as e:
        return e


@cached()
def _has_safe_dict(_type):
    # type: (Type) -> bool
    try:
        return type(object.__getattribute__(_type, "__dict__").get("__dict__")) is GetSetDescriptor
    except AttributeError:
        return False


def _safe_dict(o):
    # type: (Any) -> Dict[str, Any]
    if _has_safe_dict(type(o)):
        return object.__getattribute__(o, "__dict__")
    raise AttributeError("No safe __dict__ attribute")


@cached()
def _qualname(_type):
    # type: (Type) -> str
    try:
        return stringify(_type.__qualname__)
    except AttributeError:
        if _type.__module__ == "__builtin__":
            try:
                return _type.__name__
            except AttributeError:
                return repr(_type)
        try:
            return "%s.%s" % (_type.__module__, _type.__name__)
        except AttributeError:
            if _type.__module__ == "__builtin__":
                return repr(_type)
            return "%s.%s" % (_type.__module__, _type)


def _serialize_collection(value, brackets, level, max_len):
    # type: (Collection, str, int, int) -> str
    o, c = brackets[0], brackets[1]
    ellipsis = ", ..." if len(value) > max_len else ""
    return "".join((o, ", ".join(_serialize(_, level - 1) for _ in islice(value, max_len)), ellipsis, c))


def _serialize(value, level=1, max_len=10, max_str_len=1024):
    # type: (Any, int, int, int) -> str
    """Python object serializer.

    We provide our own serializer to avoid any potential side effects of calling
    ``str`` directly on arbitrary objects.
    """

    if _isinstance(value, CALLABLE_TYPES):
        return object.__repr__(value)

    if type(value) in BUILTIN_SIMPLE_TYPES:
        r = repr(value)
        return "".join((r[:max_str_len], "..." + ("'" if r[0] == "'" else "") if len(r) > max_str_len else ""))

    if not level:
        return repr(type(value))

    if type(value) not in BUILTIN_CONTAINER_TYPES:
        return (
            type(value).__name__
            + "("
            + ", ".join(["=".join((k, _serialize(v, level - 1))) for k, v in _get_fields(value).items()])
            + ")"
        )

    if type(value) is dict:
        return (
            "{"
            + ", ".join([": ".join((_serialize(k, level - 1), _serialize(v, level - 1))) for k, v in value.items()])
            + "}"
        )
    elif type(value) is list:
        return _serialize_collection(value, "[]", level, max_len)
    elif type(value) is tuple:
        return _serialize_collection(value, "()", level, max_len)
    elif type(value) is set:
        return _serialize_collection(value, r"{}", level, max_len) if value else "set()"

    raise TypeError("Unhandled type: %s", type(value))


def _serialize_exc_info(exc_info):
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
        "message": ", ".join([_serialize(v) for v in value.args]),
        "stacktrace": _unwind_stack(top_tb.tb_frame) if top_tb is not None else None,
    }


def _get_fields(obj):
    # type: (Any) -> Dict[str, Any]
    try:
        return _safe_dict(obj)
    except AttributeError:
        # Check for slots
        return {s: _safe_getattr(obj, s) for s in get_slots(obj)}


def _captured_value_v2(value, level=1):
    # type: (Any, int) -> Dict[str, Any]
    _type = type(value)
    data = {
        "type": _qualname(_type),
        "value": _serialize(value, level),
    }  # type: Dict[str, Any]
    if _type not in BUILTIN_TYPES:
        data["fields"] = (
            {n: _captured_value_v2(v, level - 1) for n, v in _get_fields(value).items()} if level > 0 else None
        )

    return data


def _captured_context(
    arguments,  # type: List[Tuple[str, Any]]
    _locals,  # type: List[Tuple[str, Any]]
    throwable,  # type: ExcInfoType
    level=1,  # type: int
):
    # type: (...) -> Dict[str, Any]
    try:
        arg, argval = arguments[0]
        fields = _get_fields(argval) if arg == "self" else {}
    except IndexError:
        fields = {}

    return {
        "fields": {n: _captured_value_v2(v, level) for n, v in fields.items()},
        "arguments": {n: _captured_value_v2(v, level) for n, v in arguments} if arguments is not None else {},
        "locals": {n: _captured_value_v2(v, level) for n, v in _locals} if _locals is not None else {},
        "throwable": _serialize_exc_info(throwable),
    }


_EMPTY_CAPTURED_CONTEXT = _captured_context([], [], (None, None, None), 0)


def _snapshot_v2(snapshot):
    # type (Snapshot) -> Dict[str, Any]
    now = time()
    frame = snapshot.frame
    args = list(_get_args(frame))
    _locals = list(_get_locals(frame))  # frame.f_locals.items()

    probe = snapshot.probe
    captures = {
        "entry": snapshot.entry_capture or _EMPTY_CAPTURED_CONTEXT,
        "return": snapshot.return_capture or _EMPTY_CAPTURED_CONTEXT,
    }
    if isinstance(probe, LineProbe):
        captures["lines"] = {
            probe.line: _captured_context(args, _locals, snapshot.exc_info),
        }
        location = {
            "file": probe.source_file,
            "lines": [probe.line],
        }
    elif isinstance(probe, FunctionProbe):
        location = {
            "type": probe.module,
            "method": probe.func_qname,
        }
    return {
        "id": str(uuid4()),
        "timestamp": int(now * 1e3),  # milliseconds
        "duration": snapshot.duration,  # nanoseconds
        "stack": _unwind_stack(frame),
        "captures": captures,
        "probe": {
            "id": probe.probe_id,
            "location": location,
        },
        "language": "python",
    }


def _logger_v2(snapshot):
    # type: (Snapshot) -> Dict[str, Any]
    thread = snapshot.thread
    code = snapshot.frame.f_code

    return {
        "name": code.co_filename,
        "method": code.co_name,
        "thread_name": "%s;pid:%d" % (thread.name, os.getpid()),
        "thread_id": thread.ident,
        "version": 2,
    }


def add_tags(payload):
    if not config._tags_in_qs and config.tags:
        payload["ddtags"] = config.tags


def logs_track_upload_request_v2(
    service,  # type: str
    snapshot,  # type: Snapshot
    host,  # type: Optional[str]
):
    # type: (...) -> Dict[str, Any]
    snapshot_data = _snapshot_v2(snapshot)
    top_frame = snapshot_data["stack"][0]
    if isinstance(snapshot.probe, LineProbe):
        arguments = list(snapshot_data["captures"]["lines"].values())[0]["arguments"]
        message = "%s(%s)" % (
            top_frame["function"],
            ", ".join(("=".join((n, a["value"])) for n, a in arguments.items())),
        )
    elif isinstance(snapshot.probe, FunctionProbe):
        arguments = snapshot_data["captures"]["entry"]["arguments"]
        message = "%s(%s)" % (
            snapshot.probe.func_qname,
            ", ".join(("=".join((n, a["value"])) for n, a in arguments.items())),
        )
    context = snapshot.context
    payload = {
        "service": service,
        "debugger.snapshot": snapshot_data,
        "host": host,
        "logger": _logger_v2(snapshot),
        "dd.trace_id": context.trace_id if context else None,
        "dd.span_id": context.span_id if context else None,
        "ddsource": "dd_debugger",
        "message": message,
    }
    add_tags(payload)

    return payload


class SnapshotJsonEncoder(SnapshotEncoder):
    def __init__(self, service, host=None):
        # type: (str, Optional[str]) -> None
        self._service = service
        self._host = host

    def encode(self, snapshot):
        # type: (Snapshot) -> bytes
        return json.dumps(
            logs_track_upload_request_v2(
                service=self._service,
                snapshot=snapshot,
                host=self._host,
            )
        ).encode("utf-8")

    @classmethod
    def capture_context(
        cls,
        arguments,  # type: List[Tuple[str, Any]]
        _locals,  # type: List[Tuple[str, Any]]
        throwable,  # type: ExcInfoType
        level=1,  # type: int
    ):
        # type: (...) -> Dict[str, Any]
        return _captured_context(arguments, _locals, throwable, level)


class BatchJsonEncoder(BufferedEncoder):
    def __init__(self, item_encoders, buffer_size=4 * (1 << 20), on_full=None):
        # type: (Dict[Type, Union[Encoder, Type]], int, Optional[Callable[[Any, bytes], None]]) -> None
        self._encoders = item_encoders
        self._buffer = JsonBuffer(buffer_size)
        self._lock = forksafe.Lock()
        self._on_full = on_full
        self.count = 0
        self.max_size = buffer_size - self._buffer.size

    def put(self, item):
        # type: (Union[Snapshot, str]) -> int
        encoder = self._encoders.get(type(item))
        if encoder is None:
            raise ValueError("No encoder for item type: %r" % type(item))

        return self.put_encoded(item, encoder.encode(item))

    def put_encoded(self, item, encoded):
        # type: (Union[Snapshot, str], bytes) -> int
        try:
            with self._lock:
                size = self._buffer.put(encoded)
                self.count += 1
                return size
        except BufferFull:
            if self._on_full is not None:
                self._on_full(item, encoded)
            six.reraise(*sys.exc_info())

    def encode(self):
        # type: () -> Optional[bytes]
        with self._lock:
            if self.count == 0:
                # Reclaim memory
                self._buffer._reset()
                return None

            encoded = self._buffer.flush()
            self.count = 0
            return encoded
