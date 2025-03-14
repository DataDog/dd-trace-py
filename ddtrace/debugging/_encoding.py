import abc
import json
import os
import sys
from threading import Thread
from types import FrameType
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional
from typing import Type
from typing import Union

import six

from ddtrace.debugging._config import di_config
from ddtrace.debugging._signal.model import LogSignal
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.internal import forksafe
from ddtrace.internal._encoding import BufferFull
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.cache import cachedmethod


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


def _logs_track_logger_details(thread, frame):
    # type: (Thread, FrameType) -> Dict[str, Any]
    code = frame.f_code

    return {
        "name": code.co_filename,
        "method": code.co_name,
        "thread_name": "%s;pid:%d" % (thread.name, os.getpid()),
        "thread_id": thread.ident,
        "version": 2,
    }


def add_tags(payload):
    if not di_config._tags_in_qs and di_config.tags:
        payload["ddtags"] = di_config.tags


def _build_log_track_payload(
    service,  # type: str
    signal,  # type: LogSignal
    host,  # type: Optional[str]
):
    # type: (...) -> Dict[str, Any]
    context = signal.trace_context

    payload = {
        "service": service,
        "debugger.snapshot": signal.snapshot,
        "host": host,
        "logger": _logs_track_logger_details(signal.thread, signal.frame),
        "dd.trace_id": context.trace_id if context else None,
        "dd.span_id": context.span_id if context else None,
        "ddsource": "dd_debugger",
        "message": signal.message,
        "level": "error",
        "timestamp": int(signal.timestamp * 1e3),  # milliseconds,
    }
    add_tags(payload)
    return payload


class LogSignalJsonEncoder(Encoder):
    def __init__(self, service, host=None):
        # type: (str, Optional[str]) -> None
        self._service = service
        self._host = host

    def encode(self, log_signal):
        # type: (LogSignal) -> bytes
        return json.dumps(_build_log_track_payload(self._service, log_signal, self._host)).encode("utf-8")


class BatchJsonEncoder(BufferedEncoder):
    def __init__(self, item_encoders, buffer_size=4 * (1 << 20), on_full=None):
        # type: (Dict[Type, Union[Encoder, Type]], int, Optional[Callable[[Any, bytes], None]]) -> None
        self._encoders = item_encoders
        self._buffer = JsonBuffer(buffer_size)
        self._lock = forksafe.Lock()
        self._on_full = on_full
        self.count = 0
        self.max_size = buffer_size - self._buffer.size

    @cachedmethod()
    def _lookup_encoder(self, item_class):
        # type: (Type[Any]) -> Optional[Union[Encoder, Type]]
        for ic, encoder in self._encoders.items():
            if issubclass(item_class, ic):
                return encoder
        return None

    def put(self, item):
        # type: (Union[Snapshot, str]) -> int
        encoder = self._lookup_encoder(type(item))
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
