import threading
from typing import Any
from typing import Dict
from typing import TYPE_CHECKING

from .._encoding import BufferedEncoder
from .._encoding import packb as msgpack_packb
from ..encoding import JSONEncoderV2


if TYPE_CHECKING:  # pragma: no cover
    from ..span import Span


class CIVisibilityEncoderV01(BufferedEncoder):
    content_type = "application/msgpack"
    ALLOWED_METADATA_KEYS = ("language", "library_version", "runtime-id", "env")
    PAYLOAD_FORMAT_VERSION = 1
    TEST_EVENT_VERSION = 1

    def __init__(self, *args):
        super(CIVisibilityEncoderV01, self).__init__()
        self._lock = threading.RLock()
        self._init_buffer()
        self._metadata = {}

    def __len__(self):
        with self._lock:
            return len(self.buffer)

    def set_metadata(self, metadata):
        self._metadata.update(metadata)

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
        normalized_spans = [
            CIVisibilityEncoderV01._convert_span(span, trace[0].context.dd_origin) for trace in traces for span in trace
        ]
        self._metadata = {k: v for k, v in self._metadata.items() if k in self.ALLOWED_METADATA_KEYS}
        # TODO: Split the events in several payloads as needed to avoid hitting the intake's maximum payload size.
        return msgpack_packb(
            {"version": self.PAYLOAD_FORMAT_VERSION, "metadata": {"*": self._metadata}, "events": normalized_spans}
        )

    @staticmethod
    def _convert_span(span, dd_origin):
        # type: (Span, str) -> Dict[str, Any]
        sp = JSONEncoderV2._convert_span(span)
        sp["type"] = span.span_type
        sp["duration"] = span.duration_ns
        sp["meta"] = dict(sorted(span._meta.items()))
        sp["metrics"] = dict(sorted(span._metrics.items()))
        if dd_origin is not None:
            sp["meta"].update({"_dd.origin": dd_origin})
        if span.span_type == "test":
            event_type = "test"
        else:
            event_type = "span"
        return {"version": CIVisibilityEncoderV01.TEST_EVENT_VERSION, "type": event_type, "content": sp}
