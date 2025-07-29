from __future__ import annotations

import json
import os
import threading
from typing import TYPE_CHECKING  # noqa:F401
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Tuple  # noqa:F401
from uuid import uuid4

from ddtrace.ext import SpanTypes
from ddtrace.internal._encoding import BufferedEncoder
from ddtrace.internal._encoding import packb as msgpack_packb
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.constants import EVENT_TYPE
from ddtrace.internal.ci_visibility.constants import ITR_CORRELATION_ID_TAG_NAME
from ddtrace.internal.ci_visibility.constants import MODULE_ID
from ddtrace.internal.ci_visibility.constants import MODULE_TYPE
from ddtrace.internal.ci_visibility.constants import SESSION_ID
from ddtrace.internal.ci_visibility.constants import SESSION_TYPE
from ddtrace.internal.ci_visibility.constants import SUITE_ID
from ddtrace.internal.ci_visibility.constants import SUITE_TYPE
from ddtrace.internal.ci_visibility.telemetry.payload import ENDPOINT
from ddtrace.internal.ci_visibility.telemetry.payload import record_endpoint_payload_events_count
from ddtrace.internal.ci_visibility.telemetry.payload import record_endpoint_payload_events_serialization_time
from ddtrace.internal.encoding import JSONEncoderV2
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.time import StopWatch
from ddtrace.internal.writer.writer import NoEncodableSpansError


log = get_logger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    from ddtrace._trace.span import Span  # noqa:F401


class CIVisibilityEncoderV01(BufferedEncoder):
    content_type = "application/msgpack"
    PAYLOAD_FORMAT_VERSION = 1
    TEST_SUITE_EVENT_VERSION = 1
    TEST_EVENT_VERSION = 2
    ENDPOINT_TYPE = ENDPOINT.TEST_CYCLE
    _MAX_PAYLOAD_SIZE = 5 * 1024 * 1024  # 5MB

    def __init__(self, *args):
        # DEV: args are not used here, but are used by BufferedEncoder's __cinit__() method,
        #      which is called implicitly by Cython.
        super(CIVisibilityEncoderV01, self).__init__()
        self._metadata: Dict[str, Dict[str, str]] = {}
        self._lock = threading.RLock()
        self._is_xdist_worker = os.getenv("PYTEST_XDIST_WORKER") is not None
        self._init_buffer()

    def __len__(self):
        with self._lock:
            return len(self.buffer)

    def set_metadata(self, event_type: str, metadata: Dict[str, str]):
        self._metadata.setdefault(event_type, {}).update(metadata)

    def _init_buffer(self):
        with self._lock:
            self.buffer = []

    def put(self, item):
        with self._lock:
            self.buffer.append(item)

    def encode_traces(self, traces):
        """
        Only used for LogWriter, not called for CI Visibility currently
        """
        raise NotImplementedError()

    def encode(self) -> List[Tuple[Optional[bytes], int]]:
        with self._lock:
            if not self.buffer:
                return []
            payloads = []
            with StopWatch() as sw:
                payloads = self._build_payload(self.buffer)
            record_endpoint_payload_events_serialization_time(endpoint=self.ENDPOINT_TYPE, seconds=sw.elapsed())
            self._init_buffer()
            return payloads

    def _get_parent_session(self, traces: List[List[Span]]) -> int:
        for trace in traces:
            for span in trace:
                if span.get_tag(EVENT_TYPE) == SESSION_TYPE and span.parent_id is not None and span.parent_id != 0:
                    return span.parent_id
        return 0

    def _build_payload(self, traces: List[List[Span]]) -> List[Tuple[Optional[bytes], int]]:
        """
        Build multiple payloads from traces, splitting when necessary to stay under size limits.
        Uses index-based recursive approach to avoid copying slices.

        Returns a list of (payload_bytes, trace_count) tuples, where each payload contains
        as many traces as possible without exceeding _MAX_PAYLOAD_SIZE.
        """
        if not traces:
            return []

        new_parent_session_span_id = self._get_parent_session(traces)
        return self._build_payloads_recursive(traces, 0, len(traces), new_parent_session_span_id)

    def _build_payloads_recursive(
        self, traces: List[List[Span]], start_idx: int, end_idx: int, new_parent_session_span_id: int
    ) -> List[Tuple[Optional[bytes], int]]:
        """
        Recursively build payloads using start/end indexes to avoid slice copying.

        Args:
            traces: Full list of traces
            start_idx: Start index (inclusive)
            end_idx: End index (exclusive)
            new_parent_session_span_id: Parent session span ID

        Returns:
            List of (payload_bytes, trace_count) tuples
        """
        if start_idx >= end_idx:
            return []

        trace_count = end_idx - start_idx

        # Convert traces to spans with filtering (using indexes)
        all_spans_with_trace_info = self._convert_traces_to_spans_indexed(
            traces, start_idx, end_idx, new_parent_session_span_id
        )

        # Get all spans (flattened)
        all_spans = [span for _, trace_spans in all_spans_with_trace_info for span in trace_spans]

        if not all_spans:
            log.debug("No spans to encode after filtering, skipping chunk")
            return []

        # Try to create payload from all spans
        payload = self._create_payload_from_spans(all_spans)

        if len(payload) <= self._MAX_PAYLOAD_SIZE or trace_count == 1:
            # Payload fits or we can't split further (single trace)
            record_endpoint_payload_events_count(endpoint=self.ENDPOINT_TYPE, count=len(all_spans))
            return [(payload, trace_count)]
        else:
            # Payload is too large, split in half recursively
            mid_idx = start_idx + (trace_count + 1) // 2

            # Process both halves recursively
            left_payloads = self._build_payloads_recursive(traces, start_idx, mid_idx, new_parent_session_span_id)
            right_payloads = self._build_payloads_recursive(traces, mid_idx, end_idx, new_parent_session_span_id)

            # Combine results
            return left_payloads + right_payloads

    def _convert_traces_to_spans_indexed(
        self, traces: List[List[Span]], start_idx: int, end_idx: int, new_parent_session_span_id: int
    ) -> List[Tuple[int, List[Dict[str, Any]]]]:
        """Convert traces to spans with xdist filtering applied, using indexes to avoid slicing."""
        all_spans_with_trace_info = []
        for trace_idx in range(start_idx, end_idx):
            trace = traces[trace_idx]
            trace_spans = [
                self._convert_span(span, trace[0].context.dd_origin, new_parent_session_span_id)
                for span in trace
                if (not self._is_xdist_worker) or (span.get_tag(EVENT_TYPE) != SESSION_TYPE)
            ]
            all_spans_with_trace_info.append((trace_idx, trace_spans))

        return all_spans_with_trace_info

    def _create_payload_from_spans(self, spans: List[Dict[str, Any]]) -> bytes:
        """Create a payload from the given spans."""
        return CIVisibilityEncoderV01._pack_payload(
            {
                "version": self.PAYLOAD_FORMAT_VERSION,
                "metadata": self._metadata,
                "events": spans,
            }
        )

    @staticmethod
    def _pack_payload(payload):
        return msgpack_packb(payload)

    def _convert_span(
        self, span: Span, dd_origin: Optional[str] = None, new_parent_session_span_id: int = 0
    ) -> Dict[str, Any]:
        sp = JSONEncoderV2._span_to_dict(span)
        sp = JSONEncoderV2._normalize_span(sp)
        sp["type"] = span.get_tag(EVENT_TYPE) or span.span_type
        sp["duration"] = span.duration_ns
        sp["meta"] = dict(sorted(span._meta.items()))
        sp["metrics"] = dict(sorted(span._metrics.items()))
        if dd_origin is not None:
            sp["meta"].update({"_dd.origin": dd_origin})
        sp = CIVisibilityEncoderV01._filter_ids(sp, new_parent_session_span_id)

        version = CIVisibilityEncoderV01.TEST_SUITE_EVENT_VERSION
        if span.get_tag(EVENT_TYPE) == "test":
            version = CIVisibilityEncoderV01.TEST_EVENT_VERSION

        if span.span_type == "test":
            event_type = span.get_tag(EVENT_TYPE)
        else:
            event_type = "span"

        return {"version": version, "type": event_type, "content": sp}

    @staticmethod
    def _filter_ids(sp, new_parent_session_span_id=0):
        """
        Remove trace/span/parent IDs if non-test event, move session/module/suite IDs from meta to outer content layer.
        """
        if sp["meta"].get(EVENT_TYPE) in [SESSION_TYPE, MODULE_TYPE, SUITE_TYPE]:
            del sp["trace_id"]
            del sp["span_id"]
            del sp["parent_id"]
        else:
            sp["trace_id"] = int(sp.get("trace_id") or "1")
            sp["parent_id"] = int(sp.get("parent_id") or "1")
            sp["span_id"] = int(sp.get("span_id") or "1")
        if sp["meta"].get(EVENT_TYPE) in [SESSION_TYPE, MODULE_TYPE, SUITE_TYPE, SpanTypes.TEST]:
            test_session_id = new_parent_session_span_id or sp["meta"].get(SESSION_ID)
            if test_session_id:
                sp[SESSION_ID] = int(test_session_id)
                del sp["meta"][SESSION_ID]
        if sp["meta"].get(EVENT_TYPE) in [MODULE_TYPE, SUITE_TYPE, SpanTypes.TEST]:
            test_module_id = sp["meta"].get(MODULE_ID)
            if test_module_id:
                sp[MODULE_ID] = int(test_module_id)
                del sp["meta"][MODULE_ID]
        if sp["meta"].get(EVENT_TYPE) in [SUITE_TYPE, SpanTypes.TEST]:
            test_suite_id = sp["meta"].get(SUITE_ID)
            if test_suite_id:
                sp[SUITE_ID] = int(test_suite_id)
                del sp["meta"][SUITE_ID]
        if COVERAGE_TAG_NAME in sp["meta"]:
            del sp["meta"][COVERAGE_TAG_NAME]
        if ITR_CORRELATION_ID_TAG_NAME in sp["meta"]:
            sp[ITR_CORRELATION_ID_TAG_NAME] = sp["meta"][ITR_CORRELATION_ID_TAG_NAME]
            del sp["meta"][ITR_CORRELATION_ID_TAG_NAME]
        return sp


class CIVisibilityCoverageEncoderV02(CIVisibilityEncoderV01):
    PAYLOAD_FORMAT_VERSION = 2
    ENDPOINT_TYPE = ENDPOINT.CODE_COVERAGE
    boundary = uuid4().hex
    content_type = "multipart/form-data; boundary=%s" % boundary
    itr_suite_skipping_mode = False

    def _set_itr_suite_skipping_mode(self, new_value):
        self.itr_suite_skipping_mode = new_value

    def put(self, item):
        spans_with_coverage = [
            span
            for span in item
            if COVERAGE_TAG_NAME in span.get_tags() or span.get_struct_tag(COVERAGE_TAG_NAME) is not None
        ]
        if not spans_with_coverage:
            raise NoEncodableSpansError()
        return super(CIVisibilityCoverageEncoderV02, self).put(spans_with_coverage)

    def _build_coverage_attachment(self, data: bytes) -> List[bytes]:
        return [
            b"--%s" % self.boundary.encode("utf-8"),
            b'Content-Disposition: form-data; name="coverage1"; filename="coverage1.msgpack"',
            b"Content-Type: application/msgpack",
            b"",
            data,
        ]

    def _build_event_json_attachment(self) -> List[bytes]:
        return [
            b"--%s" % self.boundary.encode("utf-8"),
            b'Content-Disposition: form-data; name="event"; filename="event.json"',
            b"Content-Type: application/json",
            b"",
            b'{"dummy":true}',
        ]

    def _build_body(self, data: bytes) -> List[bytes]:
        return (
            self._build_coverage_attachment(data)
            + self._build_event_json_attachment()
            + [b"--%s--" % self.boundary.encode("utf-8")]
        )

    def _build_data(self, traces: List[List[Span]]) -> Optional[bytes]:
        normalized_covs = [
            self._convert_span(span)
            for trace in traces
            for span in trace
            if (COVERAGE_TAG_NAME in span.get_tags() or span.get_struct_tag(COVERAGE_TAG_NAME) is not None)
        ]
        if not normalized_covs:
            return None
        record_endpoint_payload_events_count(endpoint=ENDPOINT.CODE_COVERAGE, count=len(normalized_covs))
        # TODO: Split the events in several payloads as needed to avoid hitting the intake's maximum payload size.
        return msgpack_packb({"version": self.PAYLOAD_FORMAT_VERSION, "coverages": normalized_covs})

    def _build_payload(self, traces: List[List[Span]]) -> List[Tuple[Optional[bytes], int]]:
        data = self._build_data(traces)
        if not data:
            return []
        return [(b"\r\n".join(self._build_body(data)), len(data))]

    def _convert_span(
        self, span: Span, dd_origin: Optional[str] = None, new_parent_session_span_id: int = 0
    ) -> Dict[str, Any]:
        # DEV: new_parent_session_span_id is unused here, but it is used in super class
        files: dict[str, Any] = {}

        files_struct_tag_value = span.get_struct_tag(COVERAGE_TAG_NAME)
        if files_struct_tag_value is not None and "files" in files_struct_tag_value:
            files = files_struct_tag_value["files"]
        elif COVERAGE_TAG_NAME in span.get_tags():
            files = json.loads(str(span.get_tag(COVERAGE_TAG_NAME)))["files"]

        converted_span = {
            "test_session_id": int(span.get_tag(SESSION_ID) or "1"),
            "test_suite_id": int(span.get_tag(SUITE_ID) or "1"),
            "files": files,
        }

        if not self.itr_suite_skipping_mode:
            converted_span["span_id"] = span.span_id

        log.debug("Span converted to coverage event: %s", converted_span)

        return converted_span
