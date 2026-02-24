import json
from typing import TYPE_CHECKING
from typing import Any  # noqa:F401
from typing import Optional  # noqa:F401

from ddtrace.internal.settings._agent import config as agent_config  # noqa:F401
from ddtrace.internal.threads import RLock

from ._encoding import BufferedEncoder
from ._encoding import BufferFull
from ._encoding import BufferItemTooLarge
from ._encoding import ListStringTable
from ._encoding import MsgpackEncoderV04
from ._encoding import MsgpackEncoderV05
from .compat import ensure_text
from .logger import get_logger


__all__ = [
    "AgentlessTraceJSONEncoder",
    "MsgpackEncoderV04",
    "MsgpackEncoderV05",
    "ListStringTable",
    "MSGPACK_ENCODERS",
]


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace._trace.span import Span  # noqa:F401

log = get_logger(__name__)


class _EncoderBase(object):
    """
    Encoder interface that provides the logic to encode traces and service.
    """

    def encode_traces(self, traces: list[list["Span"]]) -> str:
        """
        Encodes a list of traces, expecting a list of items where each items
        is a list of spans. Before dumping the string in a serialized format all
        traces are normalized according to the encoding format. The trace
        nesting is not changed.

        :param traces: A list of traces that should be serialized
        """
        raise NotImplementedError()

    def encode(self, obj: list[list[Any]]) -> tuple[str, int]:
        """
        Defines the underlying format used during traces or services encoding.
        This method must be implemented and should only be used by the internal
        functions.
        """
        raise NotImplementedError()

    @staticmethod
    def _span_to_dict(span: "Span") -> dict[str, Any]:
        d: dict[str, Any] = {
            "trace_id": span._trace_id_64bits,
            "parent_id": span.parent_id,
            "span_id": span.span_id,
            "service": span.service,
            "resource": span.resource,
            "name": span.name,
            "error": span.error,
        }

        # a common mistake is to set the error field to a boolean instead of an
        # int. let's special case that here, because it's sure to happen in
        # customer code.
        err = d.get("error")
        if err and type(err) == bool:
            d["error"] = 1

        if span.start_ns:
            d["start"] = span.start_ns

        if span.duration_ns:
            d["duration"] = span.duration_ns

        if span._meta:
            d["meta"] = span._meta

        if span._metrics:
            d["metrics"] = span._metrics

        if span.span_type:
            d["type"] = span.span_type

        if span._links:
            d["span_links"] = [link.to_dict() for link in span._links]

        if span._events and agent_config.trace_native_span_events:
            d["span_events"] = [dict(event) for event in span._events]

        return d


class JSONEncoder(_EncoderBase):
    content_type = "application/json"

    def encode_traces(self, traces):
        normalized_traces = [
            [JSONEncoder._normalize_span(JSONEncoder._span_to_dict(span)) for span in trace] for trace in traces
        ]
        return self.encode(normalized_traces)[0]

    @staticmethod
    def _normalize_span(span):
        # Ensure all string attributes are actually strings and not bytes
        # DEV: We are deferring meta/metrics to reduce any performance issues.
        #      Meta/metrics may still contain `bytes` and have encoding issues.
        span["resource"] = JSONEncoder._normalize_str(span["resource"])
        span["name"] = JSONEncoder._normalize_str(span["name"])
        span["service"] = JSONEncoder._normalize_str(span["service"])
        return span

    @staticmethod
    def _normalize_str(obj):
        if obj is None:
            return obj

        return ensure_text(obj, errors="backslashreplace")

    def encode(self, obj):
        return json.JSONEncoder().encode(obj), len(obj)


class JSONEncoderV2(JSONEncoder):
    """
    JSONEncoderV2 encodes traces to the new intake API format.
    """

    content_type = "application/json"

    def encode_traces(self, traces: list[list["Span"]]) -> str:
        normalized_traces = [[JSONEncoderV2._convert_span(span) for span in trace] for trace in traces]
        return self.encode({"traces": normalized_traces})[0]

    @staticmethod
    def _convert_span(span: "Span") -> dict[str, Any]:
        sp = JSONEncoderV2._span_to_dict(span)
        sp = JSONEncoderV2._normalize_span(sp)
        sp["trace_id"] = JSONEncoderV2._encode_id_to_hex(sp.get("trace_id"))
        sp["parent_id"] = JSONEncoderV2._encode_id_to_hex(sp.get("parent_id"))
        sp["span_id"] = JSONEncoderV2._encode_id_to_hex(sp.get("span_id"))
        return sp

    @staticmethod
    def _encode_id_to_hex(dd_id: Optional[int]) -> str:
        if not dd_id:
            return "0000000000000000"
        return "%0.16X" % int(dd_id)

    def encode(self, obj):
        res, _ = super().encode(obj)
        return res, len(obj.get("traces", []))


class AgentlessTraceJSONEncoder(BufferedEncoder):
    """
    Buffered encoder for the agentless JSON span intake. Buffers traces and
    produces payloads in the {"spans": [...]} format for HTTPWriter.
    """

    content_type = "application/json"

    def __init__(self, max_size: int, max_item_size: int) -> None:
        self.max_size = max_size
        self.max_item_size = max_item_size
        self._buffer: list[list["Span"]] = []
        self._size = 0
        self._lock = RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    @property
    def size(self) -> int:
        with self._lock:
            return self._size

    def put(self, item) -> None:
        with self._lock:
            if len(item) > self.max_item_size:
                raise BufferItemTooLarge(len(item))
            if len(item) + self._size > self.max_size:
                raise BufferFull(len(item) + self._size)
            self._buffer.append(item)
            self._size += len(item)

    def encode(self) -> list[tuple[Optional[bytes], int]]:
        with self._lock:
            if not self._buffer:
                return []

            spans_dict = self._spans_to_dict(self._buffer)
            json_str = json.dumps(spans_dict)
            payload_bytes = json_str.encode("utf-8")
            n_traces = len(self._buffer)
            self._buffer = []
            self._size = 0
            return [(payload_bytes, n_traces)]

    def _span_to_dict(self, span: "Span") -> dict[str, Any]:
        return {
            "trace_id": str(span._trace_id_64bits),
            "parent_id": str(span.parent_id),
            "span_id": str(span.span_id),
            "service": span.service,
            "resource": span.resource,
            "name": span.name,
            "error": span.error,
            "start": span.start_ns,
            "duration": span.duration_ns,
            "meta": span._meta,
            "metrics": span._metrics,
            "type": span.span_type,
            "span_links": [link.to_dict() for link in span._links],
        }

    def _spans_to_dict(self, traces: list[list["Span"]]) -> dict[str, Any]:
        return {"spans": [self._span_to_dict(span) for trace in traces for span in trace]}


MSGPACK_ENCODERS = {
    "v0.4": MsgpackEncoderV04,
    "v0.5": MsgpackEncoderV05,
}
