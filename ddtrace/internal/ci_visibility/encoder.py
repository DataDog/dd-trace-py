import json
import os
import threading
from typing import TYPE_CHECKING  # noqa:F401
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
    from typing import Any  # noqa:F401
    from typing import Dict  # noqa:F401
    from typing import List  # noqa:F401
    from typing import Optional  # noqa:F401

    from ddtrace._trace.span import Span  # noqa:F401


class CIVisibilityEncoderV01(BufferedEncoder):
    content_type = "application/msgpack"
    PAYLOAD_FORMAT_VERSION = 1
    TEST_SUITE_EVENT_VERSION = 1
    TEST_EVENT_VERSION = 2
    ENDPOINT_TYPE = ENDPOINT.TEST_CYCLE

    def __init__(self, *args):
        # DEV: args are not used here, but are used by BufferedEncoder's __cinit__() method,
        #      which is called implicitly by Cython.
        super(CIVisibilityEncoderV01, self).__init__()
        self._lock = threading.RLock()
        self._metadata = {}
        self._init_buffer()

    def __len__(self):
        with self._lock:
            return len(self.buffer)

    def set_metadata(self, event_type, metadata):
        # type: (str, Dict[str, str]) -> None
        self._metadata.setdefault(event_type, {}).update(metadata)

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
            with StopWatch() as sw:
                payload = self._build_payload(self.buffer)
            record_endpoint_payload_events_serialization_time(endpoint=self.ENDPOINT_TYPE, seconds=sw.elapsed())
            buffer_size = len(self.buffer)
            self._init_buffer()
            return payload, buffer_size

    def _get_parent_session(self, traces):
        for trace in traces:
            for span in trace:
                if span.get_tag(EVENT_TYPE) == SESSION_TYPE and span.parent_id is not None and span.parent_id != 0:
                    return span.parent_id
        return 0

    def _build_payload(self, traces):
        new_parent_session_span_id = self._get_parent_session(traces)
        is_not_xdist_worker = os.getenv("PYTEST_XDIST_WORKER") is None
        normalized_spans = [
            self._convert_span(span, trace[0].context.dd_origin, new_parent_session_span_id)
            for trace in traces
            for span in trace
            if (is_not_xdist_worker or span.get_tag(EVENT_TYPE) != SESSION_TYPE)
        ]
        if not normalized_spans:
            return None
        record_endpoint_payload_events_count(endpoint=ENDPOINT.TEST_CYCLE, count=len(normalized_spans))

        # TODO: Split the events in several payloads as needed to avoid hitting the intake's maximum payload size.
        return CIVisibilityEncoderV01._pack_payload(
            {"version": self.PAYLOAD_FORMAT_VERSION, "metadata": self._metadata, "events": normalized_spans}
        )

    @staticmethod
    def _pack_payload(payload):
        return msgpack_packb(payload)

    def _convert_span(self, span, dd_origin, new_parent_session_span_id=0):
        # type: (Span, str, Optional[int]) -> Dict[str, Any]
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

    def put(self, spans):
        spans_with_coverage = [
            span
            for span in spans
            if COVERAGE_TAG_NAME in span.get_tags() or span.get_struct_tag(COVERAGE_TAG_NAME) is not None
        ]
        if not spans_with_coverage:
            raise NoEncodableSpansError()
        return super(CIVisibilityCoverageEncoderV02, self).put(spans_with_coverage)

    def _build_coverage_attachment(self, data):
        # type: (bytes) -> List[bytes]
        return [
            b"--%s" % self.boundary.encode("utf-8"),
            b'Content-Disposition: form-data; name="coverage1"; filename="coverage1.msgpack"',
            b"Content-Type: application/msgpack",
            b"",
            data,
        ]

    def _build_event_json_attachment(self):
        # type: () -> List[bytes]
        return [
            b"--%s" % self.boundary.encode("utf-8"),
            b'Content-Disposition: form-data; name="event"; filename="event.json"',
            b"Content-Type: application/json",
            b"",
            b'{"dummy":true}',
        ]

    def _build_body(self, data):
        # type: (bytes) -> List[bytes]
        return (
            self._build_coverage_attachment(data)
            + self._build_event_json_attachment()
            + [b"--%s--" % self.boundary.encode("utf-8")]
        )

    def _build_data(self, traces):
        # type: (List[List[Span]]) -> Optional[bytes]
        normalized_covs = [
            self._convert_span(span, "")
            for trace in traces
            for span in trace
            if (COVERAGE_TAG_NAME in span.get_tags() or span.get_struct_tag(COVERAGE_TAG_NAME) is not None)
        ]
        if not normalized_covs:
            return None
        record_endpoint_payload_events_count(endpoint=ENDPOINT.CODE_COVERAGE, count=len(normalized_covs))
        # TODO: Split the events in several payloads as needed to avoid hitting the intake's maximum payload size.
        return msgpack_packb({"version": self.PAYLOAD_FORMAT_VERSION, "coverages": normalized_covs})

    def _build_payload(self, traces):
        # type: (List[List[Span]]) -> Optional[bytes]
        data = self._build_data(traces)
        if not data:
            return None
        return b"\r\n".join(self._build_body(data))

    def _convert_span(self, span, dd_origin, new_parent_session_span_id=0):
        # type: (Span, str, Optional[int]) -> Dict[str, Any]
        # DEV: new_parent_session_span_id is unused here, but it is used in super class
        files: Dict[str, Any] = {}

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
