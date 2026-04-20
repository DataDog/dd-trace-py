import json
import typing  # noqa:F401
from typing import TYPE_CHECKING
from typing import Any  # noqa:F401
from typing import Optional

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


def _json_dumps_bytes(obj: object) -> bytes:
    """Serialize to JSON and return UTF-8 bytes (alternative to json.dumps that returns binary)."""
    # TODO(munir): Consider vendoring orjson to avoid this intermeriate strings
    return json.dumps(obj).encode("utf-8", errors="backslashreplace")


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

        _meta = span._get_str_attributes()
        if _meta:
            d["meta"] = _meta

        _metrics = span._get_numeric_attributes()
        if _metrics:
            d["metrics"] = _metrics

        if span.span_type:
            d["type"] = span.span_type

        if span._has_links():
            d["span_links"] = [link.to_dict() for link in span._get_links()]

        if span._has_events() and agent_config.trace_native_span_events:
            d["span_events"] = [dict(event) for event in span._get_events()]

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
    Buffered encoder for the agentless JSON trace intake. Buffers multiple traces and
    produces a single payload in the {"traces": [[...], ...]} format for HTTPWriter.
    """

    content_type = "application/json"
    _PREFIX = b'{"traces":['
    _SUFFIX = b"]}"
    _SEPARATOR = b","

    def __init__(self, max_size: int, max_item_size: int) -> None:
        self.max_size = max_size
        self.max_item_size = max_item_size
        self._lock = RLock()
        self._reset()

    def _reset(self) -> None:
        self._buffer = bytearray(self._PREFIX)
        self._count = 0

    def __len__(self) -> int:
        with self._lock:
            return self._count

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._buffer) + len(self._SUFFIX)

    def put(self, item) -> None:
        item = typing.cast(list["Span"], item)

        if not item:
            return

        with self._lock:
            # First span in the list: set compute_stats in meta so intake can compute stats.
            # Root and top-level are normally set by the Agent; set them here for trace views.
            item[0]._set_attribute("_dd.compute_stats", "1")
            encoded_trace = _json_dumps_bytes({"spans": [self._item_to_dict(span) for span in item]})
            item_size = len(encoded_trace)
            if item_size > self.max_item_size:
                raise BufferItemTooLarge(item_size)

            # Projected size: current buffer + separator (if not first) + new trace + suffix
            added = item_size + (1 if self._count > 0 else 0)
            if len(self._buffer) + added + len(self._SUFFIX) > self.max_size:
                raise BufferFull(len(self._buffer) + added + len(self._SUFFIX))

            if self._count > 0:
                self._buffer += self._SEPARATOR
            self._buffer += encoded_trace
            self._count += 1

    def encode(self) -> list[tuple[Optional[bytes], int]]:
        with self._lock:
            if self._count == 0:
                return []
            self._buffer += self._SUFFIX
            payload = self._buffer
            count = self._count
            self._reset()
        return [(payload, count)]

    def _item_to_dict(self, item: "Span") -> dict[str, Any]:
        if not item.parent_id:
            item._set_attribute("_trace_root", 1)
        if item._is_top_level:
            item._set_attribute("_top_level", 1)

        span_dict = JSONEncoderV2._convert_span(item)
        span_dict["meta_struct"] = item._get_meta_structs()
        # Intake Requires ids to be in lowercase
        span_dict["trace_id"] = span_dict["trace_id"].lower()
        span_dict["parent_id"] = span_dict["parent_id"].lower()
        span_dict["span_id"] = span_dict["span_id"].lower()
        return span_dict


MSGPACK_ENCODERS = {
    "v0.4": MsgpackEncoderV04,
    "v0.5": MsgpackEncoderV05,
}
