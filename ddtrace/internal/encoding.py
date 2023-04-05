import json
import threading
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import TYPE_CHECKING

from ._encoding import BufferedEncoder
from ._encoding import ListStringTable
from ._encoding import MsgpackEncoderV03
from ._encoding import MsgpackEncoderV05
from ._encoding import packb as msgpack_packb
from .compat import PY3
from .compat import binary_type
from .compat import ensure_text
from .logger import get_logger


__all__ = ["MsgpackEncoderV03", "MsgpackEncoderV05", "ListStringTable", "MSGPACK_ENCODERS"]


if TYPE_CHECKING:  # pragma: no cover
    from ..span import Span


log = get_logger(__name__)


class _EncoderBase(object):
    """
    Encoder interface that provides the logic to encode traces and service.
    """

    def encode_traces(self, traces):
        # type: (List[List[Span]]) -> str
        """
        Encodes a list of traces, expecting a list of items where each items
        is a list of spans. Before dumping the string in a serialized format all
        traces are normalized according to the encoding format. The trace
        nesting is not changed.

        :param traces: A list of traces that should be serialized
        """
        raise NotImplementedError()

    def encode(self, obj):
        # type: (List[List[Any]]) -> str
        """
        Defines the underlying format used during traces or services encoding.
        This method must be implemented and should only be used by the internal
        functions.
        """
        raise NotImplementedError()

    @staticmethod
    def _span_to_dict(span):
        # type: (Span) -> Dict[str, Any]
        d = {
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

        return d


class JSONEncoder(json.JSONEncoder, _EncoderBase):
    content_type = "application/json"

    def encode_traces(self, traces):
        normalized_traces = [
            [JSONEncoder._normalize_span(JSONEncoder._span_to_dict(span)) for span in trace] for trace in traces
        ]
        return self.encode(normalized_traces)

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

        if PY3:
            return ensure_text(obj, errors="backslashreplace")
        elif isinstance(obj, binary_type):
            return obj.decode("utf-8", errors="replace")
        return obj


class JSONEncoderV2(JSONEncoder):
    """
    JSONEncoderV2 encodes traces to the new intake API format.
    """

    content_type = "application/json"

    def encode_traces(self, traces):
        # type: (List[List[Span]]) -> str
        normalized_traces = [[JSONEncoderV2._convert_span(span) for span in trace] for trace in traces]
        return self.encode({"traces": normalized_traces})

    @staticmethod
    def _convert_span(span):
        # type: (Span) -> Dict[str, Any]
        sp = JSONEncoderV2._span_to_dict(span)
        sp = JSONEncoderV2._normalize_span(sp)
        sp["trace_id"] = JSONEncoderV2._encode_id_to_hex(sp.get("trace_id"))
        sp["parent_id"] = JSONEncoderV2._encode_id_to_hex(sp.get("parent_id"))
        sp["span_id"] = JSONEncoderV2._encode_id_to_hex(sp.get("span_id"))
        return sp

    @staticmethod
    def _encode_id_to_hex(dd_id):
        # type: (Optional[int]) -> str
        if not dd_id:
            return "0000000000000000"
        return "%0.16X" % int(dd_id)

    @staticmethod
    def _decode_id_to_hex(hex_id):
        # type: (Optional[str]) -> int
        if not hex_id:
            return 0
        return int(hex_id, 16)


class CIVisibilityEncoderV01(BufferedEncoder):
    content_type = "application/msgpack"
    ALLOWED_METADATA_KEYS = ("language", "library_version", "runtime-id", "env")

    def __init__(self, *args, metadata=None, **kwargs):
        super(CIVisibilityEncoderV01, self).__init__()
        self._lock = threading.RLock()
        self._init_buffer()
        self._metadata = metadata or dict()

    def __len__(self):
        with self._lock:
            return len(self.buffer)

    def _init_buffer(self):
        with self._lock:
            self.buffer = []

    def put(self, spans):
        with self._lock:
            self.buffer.append(spans)

    def encode_traces(self, traces):
        return self._build_payload(traces=traces)

    def encode(self):
        with self._lock:
            payload = self._build_payload(self.buffer)
            self._init_buffer()
            return payload

    def _build_payload(self, traces):
        normalized_spans = [CIVisibilityEncoderV01._convert_span(span) for trace in traces for span in trace]
        self._metadata = {k: v for k, v in self._metadata.items() if k in self.ALLOWED_METADATA_KEYS}
        return msgpack_packb({"version": 1, "metadata": {"*": self._metadata}, "events": normalized_spans})

    @staticmethod
    def _convert_span(span):
        # type: (Span) -> Dict[str, Any]
        sp = JSONEncoderV2._convert_span(span)
        sp["type"] = span.span_type
        sp["duration"] = span.duration_ns
        sp["meta"] = dict(sorted(span._meta.items()))
        sp["metrics"] = dict(sorted(span._metrics.items()))
        if span.span_type == "test":
            event_type = "test"
        else:
            event_type = "span"
        return {"version": 1, "type": event_type, "content": sp}


MSGPACK_ENCODERS = {
    "v0.3": MsgpackEncoderV03,
    "v0.4": MsgpackEncoderV03,
    "v0.5": MsgpackEncoderV05,
}
