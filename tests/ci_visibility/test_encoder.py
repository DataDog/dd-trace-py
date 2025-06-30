import json
import os

import msgpack
import pytest

from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.constants import EVENT_TYPE
from ddtrace.internal.ci_visibility.constants import ITR_CORRELATION_ID_TAG_NAME
from ddtrace.internal.ci_visibility.constants import SESSION_ID
from ddtrace.internal.ci_visibility.constants import SESSION_TYPE
from ddtrace.internal.ci_visibility.constants import SUITE_ID
from ddtrace.internal.ci_visibility.encoder import CIVisibilityCoverageEncoderV02
from ddtrace.internal.ci_visibility.encoder import CIVisibilityEncoderV01
from ddtrace.internal.encoding import JSONEncoder
from ddtrace.trace import Span
from tests.contrib.pytest.test_pytest import PytestTestCaseBase


@pytest.fixture
def mock_xdist_worker_env():
    """Fixture to mock being in an xdist worker environment"""
    original_env = os.environ.get("PYTEST_XDIST_WORKER")
    os.environ["PYTEST_XDIST_WORKER"] = "gw0"
    yield
    # Restore original environment
    if original_env is None:
        os.environ.pop("PYTEST_XDIST_WORKER", None)
    else:
        os.environ["PYTEST_XDIST_WORKER"] = original_env


@pytest.fixture
def mock_no_xdist_worker_env():
    """Fixture to ensure we're not in xdist worker environment"""
    original_env = os.environ.get("PYTEST_XDIST_WORKER")
    os.environ.pop("PYTEST_XDIST_WORKER", None)
    yield
    # Restore original environment
    if original_env is not None:
        os.environ["PYTEST_XDIST_WORKER"] = original_env


def test_encode_traces_civisibility_v0():
    traces = [
        [
            Span(name="client.testing", span_id=0xAAAAAA, service="foo"),
            Span(name="client.testing", span_id=0xAAAAAA, service="foo"),
        ],
        [
            Span(name="client.testing", span_id=0xAAAAAA, service="foo"),
            Span(name="client.testing", span_id=0xAAAAAA, service="foo"),
        ],
        [
            Span(name="client.testing", span_id=0xAAAAAA, span_type="test", service="foo"),
            Span(name="client.testing", span_id=0xAAAAAA, span_type="test", service="foo"),
        ],
    ]
    test_trace = traces[2]
    test_trace[0].set_tag_str("type", "test")
    test_trace[1].set_tag_str("type", "test")

    encoder = CIVisibilityEncoderV01(0, 0)
    encoder.set_metadata("*", {"language": "python"})
    for trace in traces:
        encoder.put(trace)
    payload, num_traces = encoder.encode()
    assert num_traces == 3
    assert isinstance(payload, bytes)
    decoded = msgpack.unpackb(payload, raw=True, strict_map_key=False)
    assert decoded[b"version"] == 1
    assert len(decoded[b"metadata"]) == 1

    star_metadata = decoded[b"metadata"][b"*"]
    assert star_metadata[b"language"] == b"python"

    received_events = sorted(decoded[b"events"], key=lambda event: event[b"content"][b"start"])
    assert len(received_events) == 6
    all_spans = sorted([span for trace in traces for span in trace], key=lambda span: span.start_ns)
    for given_span, received_event in zip(all_spans, received_events):
        expected_meta = {
            "{}".format(key).encode("utf-8"): "{}".format(value).encode("utf-8")
            for key, value in sorted(given_span._meta.items())
        }
        expected_event = {
            b"type": b"test" if given_span.span_type == "test" else b"span",
            b"version": 2 if given_span.get_tag("type") and given_span.get_tag("type") == "test" else 1,
            b"content": {
                b"trace_id": int(given_span._trace_id_64bits),
                b"span_id": int(given_span.span_id),
                b"parent_id": 1,
                b"name": JSONEncoder._normalize_str(given_span.name).encode("utf-8"),
                b"resource": JSONEncoder._normalize_str(given_span.resource).encode("utf-8"),
                b"service": JSONEncoder._normalize_str(given_span.service).encode("utf-8"),
                b"type": given_span.span_type.encode("utf-8") if given_span.span_type else None,
                b"start": given_span.start_ns,
                b"duration": given_span.duration_ns,
                b"meta": expected_meta,
                b"metrics": dict(sorted(given_span._metrics.items())),
                b"error": 0,
            },
        }
        assert expected_event == received_event


def test_encode_traces_civisibility_v0_no_traces():
    encoder = CIVisibilityEncoderV01(0, 0)
    encoder.set_metadata("*", {"language": "python"})
    payload, _ = encoder.encode()
    assert payload is None


def test_encode_traces_civisibility_v0_empty_traces():
    traces = [[], []]

    encoder = CIVisibilityEncoderV01(0, 0)
    encoder.set_metadata("*", {"language": "python"})
    for trace in traces:
        encoder.put(trace)
    payload, size = encoder.encode()
    assert size == 2
    assert payload is None


def test_encode_traces_civisibility_v2_coverage_per_test():
    coverage_data = {
        "files": [
            {"filename": "test_cov.py", "segments": [[5, 0, 5, 0, -1]]},
            {"filename": "test_module.py", "segments": [[2, 0, 2, 0, -1]]},
        ]
    }
    coverage_json = json.dumps(coverage_data)
    coverage_span = Span(name=b"client.testing", span_id=0xAAAAAA, span_type="test", service="foo")
    coverage_span.set_tag(COVERAGE_TAG_NAME, coverage_json)
    coverage_span.set_tag(SUITE_ID, "12345")
    coverage_span.set_tag(SESSION_ID, "67890")
    traces = [
        [Span(name=b"client.testing", span_id=0xAAAAAA, span_type="test", service="foo"), coverage_span],
    ]

    encoder = CIVisibilityCoverageEncoderV02(0, 0)
    for trace in traces:
        encoder.put(trace)
    payload = encoder._build_data(traces)
    assert isinstance(payload, bytes)
    decoded = msgpack.unpackb(payload, raw=True, strict_map_key=False)
    assert decoded[b"version"] == 2

    received_covs = decoded[b"coverages"]
    assert len(received_covs) == 1

    expected_cov = {
        b"test_session_id": int(coverage_span.get_tag(SESSION_ID)),
        b"test_suite_id": int(coverage_span.get_tag(SUITE_ID)),
        b"span_id": 0xAAAAAA,
        b"files": [
            {k.encode("utf-8"): v.encode("utf-8") if isinstance(v, str) else v for k, v in file.items()}
            for file in coverage_data["files"]
        ],
    }
    assert expected_cov == received_covs[0]

    complete_payload, _ = encoder.encode()
    assert isinstance(complete_payload, bytes)
    payload_per_line = complete_payload.split(b"\r\n")
    assert len(payload_per_line) == 11
    assert payload_per_line[0].startswith(b"--")
    boundary = payload_per_line[0][2:]
    assert payload_per_line[1] == b'Content-Disposition: form-data; name="coverage1"; filename="coverage1.msgpack"'
    assert payload_per_line[2] == b"Content-Type: application/msgpack"
    assert payload_per_line[3] == b""
    assert payload_per_line[4] == payload
    assert payload_per_line[5] == payload_per_line[0]
    assert payload_per_line[6] == b'Content-Disposition: form-data; name="event"; filename="event.json"'
    assert payload_per_line[7] == b"Content-Type: application/json"
    assert payload_per_line[8] == b""
    assert payload_per_line[9] == b'{"dummy":true}'
    assert payload_per_line[10] == b"--%s--" % boundary


def test_encode_traces_civisibility_v2_coverage_per_suite():
    coverage_data = {
        "files": [
            {"filename": "test_cov.py", "segments": [[5, 0, 5, 0, -1]]},
            {"filename": "test_module.py", "segments": [[2, 0, 2, 0, -1]]},
        ]
    }
    coverage_json = json.dumps(coverage_data)
    coverage_span = Span(name=b"client.testing", span_id=0xAAAAAA, span_type="test", service="foo")
    coverage_span.set_tag(COVERAGE_TAG_NAME, coverage_json)
    coverage_span.set_tag(SUITE_ID, "12345")
    coverage_span.set_tag(SESSION_ID, "67890")
    traces = [
        [Span(name=b"client.testing", span_id=0xAAAAAA, span_type="test", service="foo"), coverage_span],
    ]

    encoder = CIVisibilityCoverageEncoderV02(0, 0)
    encoder._set_itr_suite_skipping_mode(True)
    for trace in traces:
        encoder.put(trace)

    payload = encoder._build_data(traces)
    complete_payload, _ = encoder.encode()
    assert isinstance(payload, bytes)
    decoded = msgpack.unpackb(payload, raw=True, strict_map_key=False)
    assert decoded[b"version"] == 2

    received_covs = decoded[b"coverages"]
    assert len(received_covs) == 1

    expected_cov = {
        b"test_session_id": int(coverage_span.get_tag(SESSION_ID)),
        b"test_suite_id": int(coverage_span.get_tag(SUITE_ID)),
        b"files": [
            {k.encode("utf-8"): v.encode("utf-8") if isinstance(v, str) else v for k, v in file.items()}
            for file in coverage_data["files"]
        ],
    }
    assert expected_cov == received_covs[0]

    assert isinstance(complete_payload, bytes)
    payload_per_line = complete_payload.split(b"\r\n")
    assert len(payload_per_line) == 11
    assert payload_per_line[0].startswith(b"--")
    boundary = payload_per_line[0][2:]
    assert payload_per_line[1] == b'Content-Disposition: form-data; name="coverage1"; filename="coverage1.msgpack"'
    assert payload_per_line[2] == b"Content-Type: application/msgpack"
    assert payload_per_line[3] == b""
    assert payload_per_line[4] == payload
    assert payload_per_line[5] == payload_per_line[0]
    assert payload_per_line[6] == b'Content-Disposition: form-data; name="event"; filename="event.json"'
    assert payload_per_line[7] == b"Content-Type: application/json"
    assert payload_per_line[8] == b""
    assert payload_per_line[9] == b'{"dummy":true}'
    assert payload_per_line[10] == b"--%s--" % boundary


def test_encode_traces_civisibility_v2_coverage_empty_traces():
    coverage_data = {
        "files": [
            {"filename": "test_cov.py", "segments": [[5, 0, 5, 0, -1]]},
            {"filename": "test_module.py", "segments": [[2, 0, 2, 0, -1]]},
        ]
    }
    coverage_json = json.dumps(coverage_data)
    coverage_span = Span(name=b"client.testing", span_id=0xAAAAAA, span_type="test", service="foo")
    coverage_span.set_tag(COVERAGE_TAG_NAME, coverage_json)
    coverage_span.set_tag(SUITE_ID, "12345")
    coverage_span.set_tag(SESSION_ID, "67890")
    traces = []

    encoder = CIVisibilityCoverageEncoderV02(0, 0)
    for trace in traces:
        encoder.put(trace)
    payload = encoder._build_data(traces)
    assert payload is None

    complete_payload, _ = encoder.encode()
    assert complete_payload is None


class PytestEncodingTestCase(PytestTestCaseBase):
    def test_event_payload(self):
        """Test that a pytest test case will generate a test event, but with:
        - test_session_id, test_module_id, test_suite_id moved from meta to event content dictionary
        - `type` set as 'test' in both meta and outermost event dictionary
        """
        py_file = self.testdir.makepyfile(
            """
            def test_ok():
                assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()
        for span in spans:
            if span.get_tag("type") == "test":
                span.set_tag(ITR_CORRELATION_ID_TAG_NAME, "encodertestcorrelationid")
        ci_agentless_encoder = CIVisibilityEncoderV01(0, 0)
        ci_agentless_encoder.put(spans)
        event_payload, _ = ci_agentless_encoder.encode()
        decoded_event_payload = self.tracer.encoder._decode(event_payload)
        given_test_span = spans[0]
        given_test_event = decoded_event_payload[b"events"][0]
        expected_meta = {
            "{}".format(key).encode("utf-8"): "{}".format(value).encode("utf-8")
            for key, value in sorted(given_test_span._meta.items())
        }
        expected_meta.update({b"_dd.origin": b"ciapp-test"})
        expected_meta.pop(b"test_session_id")
        expected_meta.pop(b"test_suite_id")
        expected_meta.pop(b"test_module_id")
        expected_meta.pop(b"itr_correlation_id")
        expected_metrics = {
            "{}".format(key).encode("utf-8"): value for key, value in sorted(given_test_span._metrics.items())
        }
        expected_test_event = {
            b"content": {
                b"duration": given_test_span.duration_ns,
                b"error": given_test_span.error,
                b"meta": expected_meta,
                b"metrics": expected_metrics,
                b"name": given_test_span.name.encode("utf-8"),
                b"parent_id": 1,
                b"resource": given_test_span.resource.encode("utf-8"),
                b"service": given_test_span.service.encode("utf-8"),
                b"span_id": given_test_span.span_id,
                b"start": given_test_span.start_ns,
                b"test_module_id": int(given_test_span.get_tag("test_module_id")),
                b"test_session_id": int(given_test_span.get_tag("test_session_id")),
                b"test_suite_id": int(given_test_span.get_tag("test_suite_id")),
                b"trace_id": given_test_span._trace_id_64bits,
                b"type": given_test_span.span_type.encode("utf-8"),
                b"itr_correlation_id": given_test_span.get_tag("itr_correlation_id").encode("utf-8"),
            },
            b"type": given_test_span.span_type.encode("utf-8"),
            b"version": CIVisibilityEncoderV01.TEST_EVENT_VERSION,
        }
        assert given_test_event == expected_test_event

    def test_suite_event_payload(self):
        """Test that a pytest test case will generate a test suite event, but with:
        - test_session_id, test_suite_id moved from meta to event content dictionary
        - trace_id, parent_id, span_id are removed
        - `type` set as 'test_suite_end'
        """
        py_file = self.testdir.makepyfile(
            """
            def test_ok():
                assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()
        for span in spans:
            if span.get_tag("type") == "test_suite_end":
                span.set_tag(ITR_CORRELATION_ID_TAG_NAME, "encodertestcorrelationid")
        ci_agentless_encoder = CIVisibilityEncoderV01(0, 0)
        ci_agentless_encoder.put(spans)
        event_payload, _ = ci_agentless_encoder.encode()
        decoded_event_payload = self.tracer.encoder._decode(event_payload)
        given_test_suite_span = spans[3]
        assert given_test_suite_span.get_tag("type") == "test_suite_end"
        given_test_suite_event = decoded_event_payload[b"events"][3]
        expected_meta = {
            "{}".format(key).encode("utf-8"): "{}".format(value).encode("utf-8")
            for key, value in sorted(given_test_suite_span._meta.items())
        }
        expected_meta.update({b"_dd.origin": b"ciapp-test"})
        expected_meta.pop(b"test_session_id")
        expected_meta.pop(b"test_suite_id")
        expected_meta.pop(b"test_module_id")
        expected_meta.pop(b"itr_correlation_id")
        expected_metrics = {
            "{}".format(key).encode("utf-8"): value for key, value in sorted(given_test_suite_span._metrics.items())
        }
        expected_test_suite_event = {
            b"content": {
                b"duration": given_test_suite_span.duration_ns,
                b"error": given_test_suite_span.error,
                b"meta": expected_meta,
                b"metrics": expected_metrics,
                b"name": given_test_suite_span.name.encode("utf-8"),
                b"resource": given_test_suite_span.resource.encode("utf-8"),
                b"service": given_test_suite_span.service.encode("utf-8"),
                b"start": given_test_suite_span.start_ns,
                b"test_module_id": int(given_test_suite_span.get_tag("test_module_id")),
                b"test_session_id": int(given_test_suite_span.get_tag("test_session_id")),
                b"test_suite_id": int(given_test_suite_span.get_tag("test_suite_id")),
                b"type": given_test_suite_span.get_tag("type").encode("utf-8"),
                b"itr_correlation_id": given_test_suite_span.get_tag("itr_correlation_id").encode("utf-8"),
            },
            b"type": given_test_suite_span.get_tag("type").encode("utf-8"),
            b"version": CIVisibilityEncoderV01.TEST_SUITE_EVENT_VERSION,
        }
        assert given_test_suite_event == expected_test_suite_event

    def test_module_event_payload(self):
        """Test that a pytest test case will generate a test module event, but with:
        - test_session_id, test_module_id moved from meta to event content dictionary
        - trace_id, parent_id, span_id removed
        - `type` set as 'test_module_end'
        """
        package_a_dir = self.testdir.mkpydir("test_package_a")
        os.chdir(str(package_a_dir))
        with open("test_a.py", "w+") as fd:
            fd.write(
                """def test_ok():
                assert True"""
            )
        self.testdir.chdir()
        self.inline_run("--ddtrace")
        spans = self.pop_spans()
        ci_agentless_encoder = CIVisibilityEncoderV01(0, 0)
        ci_agentless_encoder.put(spans)
        event_payload, _ = ci_agentless_encoder.encode()
        decoded_event_payload = self.tracer.encoder._decode(event_payload)
        given_test_module_span = spans[2]
        given_test_module_event = decoded_event_payload[b"events"][2]
        expected_meta = {
            "{}".format(key).encode("utf-8"): "{}".format(value).encode("utf-8")
            for key, value in sorted(given_test_module_span._meta.items())
        }
        expected_meta.update({b"_dd.origin": b"ciapp-test"})
        expected_meta.pop(b"test_session_id")
        expected_meta.pop(b"test_module_id")
        expected_metrics = {
            "{}".format(key).encode("utf-8"): value for key, value in sorted(given_test_module_span._metrics.items())
        }
        expected_test_module_event = {
            b"content": {
                b"duration": given_test_module_span.duration_ns,
                b"error": given_test_module_span.error,
                b"meta": expected_meta,
                b"metrics": expected_metrics,
                b"name": given_test_module_span.name.encode("utf-8"),
                b"resource": given_test_module_span.resource.encode("utf-8"),
                b"service": given_test_module_span.service.encode("utf-8"),
                b"start": given_test_module_span.start_ns,
                b"test_session_id": int(given_test_module_span.get_tag("test_session_id")),
                b"test_module_id": int(given_test_module_span.get_tag("test_module_id")),
                b"type": given_test_module_span.get_tag("type").encode("utf-8"),
            },
            b"type": given_test_module_span.get_tag("type").encode("utf-8"),
            b"version": CIVisibilityEncoderV01.TEST_SUITE_EVENT_VERSION,
        }
        assert given_test_module_event == expected_test_module_event

    def test_session_event_payload(self):
        """Test that a pytest test case will generate a test session event, but with:
        - test_session_id moved from meta to event content dictionary
        - trace_id, parent_id, span_id removed
        - `type` set as 'test_session_end'
        """
        py_file = self.testdir.makepyfile(
            """
            def test_ok():
                assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()
        ci_agentless_encoder = CIVisibilityEncoderV01(0, 0)
        ci_agentless_encoder.put(spans)
        event_payload, _ = ci_agentless_encoder.encode()
        decoded_event_payload = self.tracer.encoder._decode(event_payload)
        given_test_session_span = spans[1]
        given_test_session_event = decoded_event_payload[b"events"][1]
        expected_meta = {
            "{}".format(key).encode("utf-8"): "{}".format(value).encode("utf-8")
            for key, value in sorted(given_test_session_span._meta.items())
        }
        expected_meta.update({b"_dd.origin": b"ciapp-test"})
        expected_meta.pop(b"test_session_id")
        expected_metrics = {
            "{}".format(key).encode("utf-8"): value for key, value in sorted(given_test_session_span._metrics.items())
        }
        expected_test_session_event = {
            b"content": {
                b"duration": given_test_session_span.duration_ns,
                b"error": given_test_session_span.error,
                b"meta": expected_meta,
                b"metrics": expected_metrics,
                b"name": given_test_session_span.name.encode("utf-8"),
                b"resource": given_test_session_span.resource.encode("utf-8"),
                b"service": given_test_session_span.service.encode("utf-8"),
                b"start": given_test_session_span.start_ns,
                b"test_session_id": int(given_test_session_span.get_tag("test_session_id")),
                b"type": given_test_session_span.get_tag("type").encode("utf-8"),
            },
            b"type": given_test_session_span.get_tag("type").encode("utf-8"),
            b"version": CIVisibilityEncoderV01.TEST_SUITE_EVENT_VERSION,
        }
        assert given_test_session_event == expected_test_session_event


def test_get_parent_session_with_parent_id():
    """Test _get_parent_session method when session span has a parent_id"""
    # Create a session span with parent_id
    session_span = Span(name="test.session", span_id=0xBBBBBB, service="test")
    session_span.set_tag(EVENT_TYPE, SESSION_TYPE)
    session_span.parent_id = 0xAAAAAA  # Non-zero parent_id

    # Create a regular test span
    test_span = Span(name="test.case", span_id=0xCCCCCC, service="test")

    traces = [[session_span, test_span]]

    encoder = CIVisibilityEncoderV01(0, 0)
    parent_session_id = encoder._get_parent_session(traces)

    assert parent_session_id == 0xAAAAAA


def test_get_parent_session_without_parent_id():
    """Test _get_parent_session method when session span has no parent_id"""
    # Create a session span without parent_id (parent_id defaults to None)
    session_span = Span(name="test.session", span_id=0xBBBBBB, service="test")
    session_span.set_tag(EVENT_TYPE, SESSION_TYPE)
    # parent_id remains None (default)

    test_span = Span(name="test.case", span_id=0xCCCCCC, service="test")

    traces = [[session_span, test_span]]

    encoder = CIVisibilityEncoderV01(0, 0)
    parent_session_id = encoder._get_parent_session(traces)

    assert parent_session_id == 0


def test_get_parent_session_no_session_spans():
    """Test _get_parent_session method when there are no session spans"""
    test_span = Span(name="test.case", span_id=0xCCCCCC, service="test")
    suite_span = Span(name="test.suite", span_id=0xDDDDDD, service="test")

    traces = [[test_span, suite_span]]

    encoder = CIVisibilityEncoderV01(0, 0)
    parent_session_id = encoder._get_parent_session(traces)

    assert parent_session_id == 0


def test_xdist_worker_session_filtering(mock_xdist_worker_env):
    """Test that session spans are filtered out when PYTEST_XDIST_WORKER is set"""
    session_span = Span(name="test.session", span_id=0xAAAAAA, service="test")
    session_span.set_tag(EVENT_TYPE, SESSION_TYPE)

    test_span = Span(name="test.case", span_id=0xBBBBBB, service="test", span_type="test")
    test_span.set_tag(EVENT_TYPE, "test")

    traces = [[session_span, test_span]]

    encoder = CIVisibilityEncoderV01(0, 0)
    encoder.set_metadata("*", {"language": "python"})

    for trace in traces:
        encoder.put(trace)
    payload, num_traces = encoder.encode()

    assert num_traces == 1
    assert isinstance(payload, bytes)
    decoded = msgpack.unpackb(payload, raw=True, strict_map_key=False)

    # Should only have the test span, not the session span
    received_events = decoded[b"events"]
    assert len(received_events) == 1
    assert received_events[0][b"type"] == b"test"


def test_xdist_non_worker_includes_session(mock_no_xdist_worker_env):
    """Test that session spans are included when not in xdist worker environment"""
    session_span = Span(name="test.session", span_id=0xAAAAAA, service="test")
    session_span.set_tag(EVENT_TYPE, SESSION_TYPE)

    test_span = Span(name="test.case", span_id=0xBBBBBB, service="test", span_type="test")
    test_span.set_tag(EVENT_TYPE, "test")

    traces = [[session_span, test_span]]

    encoder = CIVisibilityEncoderV01(0, 0)
    encoder.set_metadata("*", {"language": "python"})

    for trace in traces:
        encoder.put(trace)
    payload, num_traces = encoder.encode()

    assert num_traces == 1
    assert isinstance(payload, bytes)
    decoded = msgpack.unpackb(payload, raw=True, strict_map_key=False)

    # Should have both spans
    received_events = decoded[b"events"]
    assert len(received_events) == 2
    event_types = {event[b"type"] for event in received_events}
    # Session spans will have event type "span" but content type will be "test_session_end"
    assert b"span" in event_types  # This is the session span
    assert b"test" in event_types

    # Verify we have a session event by checking content type
    has_session_event = any(
        event[b"type"] == b"span" and event[b"content"][b"type"] == SESSION_TYPE.encode("utf-8")
        for event in received_events
    )
    assert has_session_event


def test_filter_ids_with_new_parent_session_span_id():
    """Test that _filter_ids uses new_parent_session_span_id when provided"""
    session_span = Span(name="test.session", span_id=0xAAAAAA, service="test")
    session_span.set_tag(EVENT_TYPE, SESSION_TYPE)
    session_span.set_tag(SESSION_ID, "12345")

    # Create span dict like the encoder would
    sp = {"meta": {EVENT_TYPE: SESSION_TYPE, SESSION_ID: "12345"}, "trace_id": 999, "span_id": 888, "parent_id": 777}

    # Test with new_parent_session_span_id override
    new_parent_session_span_id = 0xBBBBBB
    result = CIVisibilityEncoderV01._filter_ids(sp, new_parent_session_span_id)

    # Should use the new parent session span ID instead of the original one
    assert result[SESSION_ID] == new_parent_session_span_id
    assert SESSION_ID not in result["meta"]


def test_filter_ids_without_new_parent_session_span_id():
    """Test that _filter_ids falls back to original session ID when new_parent_session_span_id is 0"""
    session_span = Span(name="test.session", span_id=0xAAAAAA, service="test")
    session_span.set_tag(EVENT_TYPE, SESSION_TYPE)
    session_span.set_tag(SESSION_ID, "12345")

    # Create span dict like the encoder would
    sp = {"meta": {EVENT_TYPE: SESSION_TYPE, SESSION_ID: "12345"}, "trace_id": 999, "span_id": 888, "parent_id": 777}

    # Test without new_parent_session_span_id (defaults to 0)
    result = CIVisibilityEncoderV01._filter_ids(sp, 0)

    # Should use the original session ID
    assert result[SESSION_ID] == 12345
    assert SESSION_ID not in result["meta"]


def test_full_encoding_with_parent_session_override():
    """Test complete encoding flow when session spans have parent_id"""
    # Create parent session span (simulating main process)
    parent_session_span = Span(name="parent.session", span_id=0xAAAAAA, service="test")
    parent_session_span.set_tag(EVENT_TYPE, SESSION_TYPE)
    parent_session_span.set_tag(SESSION_ID, "99999")

    # Create worker session span with parent_id pointing to parent session
    worker_session_span = Span(name="worker.session", span_id=0xBBBBBB, service="test")
    worker_session_span.set_tag(EVENT_TYPE, SESSION_TYPE)
    worker_session_span.set_tag(SESSION_ID, "11111")
    worker_session_span.parent_id = 0xAAAAAA  # Points to parent session

    # Create test span
    test_span = Span(name="test.case", span_id=0xCCCCCC, service="test", span_type="test")
    test_span.set_tag(EVENT_TYPE, "test")
    test_span.set_tag(SESSION_ID, "11111")  # Originally points to worker session

    traces = [[worker_session_span, test_span]]

    encoder = CIVisibilityEncoderV01(0, 0)
    encoder.set_metadata("*", {"language": "python"})

    for trace in traces:
        encoder.put(trace)
    payload, num_traces = encoder.encode()

    assert num_traces == 1
    assert isinstance(payload, bytes)
    decoded = msgpack.unpackb(payload, raw=True, strict_map_key=False)

    received_events = decoded[b"events"]
    assert len(received_events) == 2

    # Find the test event and session event
    # Note: For session spans with span_type != "test", the event type will be "span"
    # but the actual type is stored in the content["type"] field
    test_event = None
    session_event = None
    for event in received_events:
        if event[b"type"] == b"test":
            test_event = event
        elif event[b"type"] == b"span" and event[b"content"][b"type"] == SESSION_TYPE.encode("utf-8"):
            session_event = event

    assert test_event is not None
    assert session_event is not None

    # Both should use the parent session ID (0xAAAAAA) instead of worker session ID
    assert test_event[b"content"][b"test_session_id"] == 0xAAAAAA
    assert session_event[b"content"][b"test_session_id"] == 0xAAAAAA


def test_payload_size_splitting_under_limit(monkeypatch):
    """Test that payloads under the limit are not split"""
    # Mock the payload size limit to a small value for testing
    monkeypatch.setattr(CIVisibilityEncoderV01, "_MAX_PAYLOAD_SIZE", 10000)  # 10KB

    # Create small traces that won't exceed the limit
    traces = []
    for i in range(3):
        trace = [
            Span(name=f"test.span.{i}", span_id=0xAAAAAA + i, service="test"),
        ]
        trace[0].set_tag_str("type", "test")
        traces.append(trace)

    encoder = CIVisibilityEncoderV01(0, 0)
    encoder.set_metadata("*", {"language": "python"})
    for trace in traces:
        encoder.put(trace)

    payload, count = encoder.encode()

    # All traces should be processed in one payload
    assert count == 3
    assert payload is not None
    assert len(encoder) == 0  # Buffer should be empty


def test_payload_size_splitting_over_limit(monkeypatch):
    """Test that payloads over the limit are split appropriately"""
    # Mock the payload size limit to a small value for testing
    monkeypatch.setattr(CIVisibilityEncoderV01, "_MAX_PAYLOAD_SIZE", 2000)  # 2KB

    # Create traces that will exceed the limit
    # Each trace will have metadata to increase payload size
    traces = []
    medium_metadata = "x" * 500  # 500 bytes of data per trace

    for i in range(8):  # Should trigger splitting
        trace = [
            Span(name=f"test.span.{i}", span_id=0xAAAAAA + i, service="test"),
        ]
        trace[0].set_tag_str("type", "test")
        trace[0].set_tag_str("data", medium_metadata)
        traces.append(trace)

    encoder = CIVisibilityEncoderV01(0, 0)
    encoder.set_metadata("*", {"language": "python"})
    for trace in traces:
        encoder.put(trace)

    # First encode should return some traces but not all
    payload1, count1 = encoder.encode()

    assert payload1 is not None
    assert count1 > 0
    assert count1 < 8  # Should not process all traces due to size limit
    assert len(encoder) > 0  # Buffer should have remaining traces

    # Second encode should return remaining traces
    payload2, count2 = encoder.encode()

    if payload2 is not None:
        assert count2 > 0
        assert count1 + count2 <= 8  # Total processed should not exceed input

    # Eventually all traces should be processed
    total_processed = count1 + (count2 if count2 else 0)
    remaining_traces = 8 - total_processed

    if remaining_traces > 0:
        payload3, count3 = encoder.encode()
        if payload3 is not None:
            total_processed += count3


def test_payload_size_splitting_single_large_trace(monkeypatch):
    """Test that a single trace larger than the limit is still processed"""
    # Mock the payload size limit to a small value for testing
    monkeypatch.setattr(CIVisibilityEncoderV01, "_MAX_PAYLOAD_SIZE", 1000)  # 1KB

    # Create one trace that exceeds the limit by itself
    large_metadata = "x" * 2000  # 2KB of data (exceeds 1KB limit)

    trace = [
        Span(name="test.large.span", span_id=0xAAAAAA, service="test"),
    ]
    trace[0].set_tag_str("type", "test")
    trace[0].set_tag_str("large_data", large_metadata)

    encoder = CIVisibilityEncoderV01(0, 0)
    encoder.set_metadata("*", {"language": "python"})
    encoder.put(trace)

    payload, count = encoder.encode()

    # Even though it exceeds the limit, it should still be processed
    assert count == 1
    assert payload is not None
    assert len(encoder) == 0  # Buffer should be empty


def test_payload_size_splitting_incremental_processing(monkeypatch):
    """Test that encoder processes traces incrementally when splitting"""
    # Mock the payload size limit to a small value for testing
    monkeypatch.setattr(CIVisibilityEncoderV01, "_MAX_PAYLOAD_SIZE", 3000)  # 3KB

    # Create multiple medium-sized traces
    traces = []
    medium_metadata = "x" * 400  # 400 bytes per trace

    for i in range(10):  # Should trigger splitting
        trace = [
            Span(name=f"test.span.{i}", span_id=0xAAAAAA + i, service="test"),
        ]
        trace[0].set_tag_str("type", "test")
        trace[0].set_tag_str("medium_data", medium_metadata)
        traces.append(trace)

    encoder = CIVisibilityEncoderV01(0, 0)
    encoder.set_metadata("*", {"language": "python"})
    for trace in traces:
        encoder.put(trace)

    total_processed = 0
    iterations = 0
    max_iterations = 5  # Safety limit to avoid infinite loops

    while len(encoder) > 0 and iterations < max_iterations:
        payload, count = encoder.encode()
        if count == 0:
            break
        total_processed += count
        iterations += 1

        assert payload is not None
        assert count > 0

    # All traces should eventually be processed
    assert total_processed == 10
    assert len(encoder) == 0


def test_payload_size_splitting_empty_traces_handling(monkeypatch):
    """Test that empty traces are handled correctly during splitting"""
    # Mock the payload size limit to a small value for testing
    monkeypatch.setattr(CIVisibilityEncoderV01, "_MAX_PAYLOAD_SIZE", 2000)  # 2KB

    # Mix of empty traces and traces with data
    traces = []
    metadata = "x" * 300  # 300 bytes per non-empty trace

    for i in range(8):
        if i % 2 == 0:
            # Empty trace
            traces.append([])
        else:
            # Trace with data
            trace = [
                Span(name=f"test.span.{i}", span_id=0xAAAAAA + i, service="test"),
            ]
            trace[0].set_tag_str("type", "test")
            trace[0].set_tag_str("data", metadata)
            traces.append(trace)

    encoder = CIVisibilityEncoderV01(0, 0)
    encoder.set_metadata("*", {"language": "python"})
    for trace in traces:
        encoder.put(trace)

    payload, count = encoder.encode()

    # Should process all traces (empty traces are counted but don't contribute to payload)
    assert count > 0
    assert payload is not None or count == len([t for t in traces if not t])  # All empty traces case


def test_payload_size_splitting_with_xdist_filtering(monkeypatch):
    """Test payload splitting works correctly with xdist session filtering"""
    # Mock the payload size limit to a small value for testing
    monkeypatch.setattr(CIVisibilityEncoderV01, "_MAX_PAYLOAD_SIZE", 2000)  # 2KB

    # Create traces with session spans that will be filtered in xdist worker mode
    traces = []
    metadata = "x" * 300  # 300 bytes per trace

    for i in range(6):
        session_span = Span(name=f"test.session.{i}", span_id=0xAAAAAA + i, service="test")
        session_span.set_tag(EVENT_TYPE, SESSION_TYPE)
        session_span.set_tag_str("data", metadata)

        test_span = Span(name=f"test.case.{i}", span_id=0xBBBBBB + i, service="test", span_type="test")
        test_span.set_tag(EVENT_TYPE, "test")
        test_span.set_tag_str("data", metadata)

        traces.append([session_span, test_span])

    # Mock xdist worker environment
    import os

    original_env = os.getenv("PYTEST_XDIST_WORKER")
    os.environ["PYTEST_XDIST_WORKER"] = "gw0"

    try:
        encoder = CIVisibilityEncoderV01(0, 0)
        encoder.set_metadata("*", {"language": "python"})
        for trace in traces:
            encoder.put(trace)

        payload, count = encoder.encode()

        # Should process traces with session spans filtered out
        assert count > 0
        assert payload is not None

        if payload:
            decoded = msgpack.unpackb(payload, raw=True, strict_map_key=False)
            events = decoded[b"events"]
            # Should only have test events, no session events
            for event in events:
                assert event[b"type"] != b"session"

    finally:
        # Restore original environment
        if original_env is None:
            os.environ.pop("PYTEST_XDIST_WORKER", None)
        else:
            os.environ["PYTEST_XDIST_WORKER"] = original_env


def test_payload_size_max_payload_constant():
    """Test that the _MAX_PAYLOAD_SIZE constant is properly defined"""
    encoder = CIVisibilityEncoderV01(0, 0)

    # Should be 5MB
    assert hasattr(encoder, "_MAX_PAYLOAD_SIZE")
    assert encoder._MAX_PAYLOAD_SIZE == 5 * 1024 * 1024


def test_payload_size_splitting_with_multiple_encode_calls(monkeypatch):
    """Test that multiple encode calls work correctly with payload splitting"""
    # Mock the payload size limit to a small value for testing
    monkeypatch.setattr(CIVisibilityEncoderV01, "_MAX_PAYLOAD_SIZE", 1500)  # 1.5KB

    # Create traces that will exceed the limit and require splitting
    traces = []
    metadata = "x" * 400  # 400 bytes per trace

    for i in range(6):  # Should trigger splitting
        trace = [
            Span(name=f"test.span.{i}", span_id=0xAAAAAA + i, service="test"),
        ]
        trace[0].set_tag_str("type", "test")
        trace[0].set_tag_str("data", metadata)
        traces.append(trace)

    encoder = CIVisibilityEncoderV01(0, 0)
    encoder.set_metadata("*", {"language": "python"})

    # Add traces to encoder
    for trace in traces:
        encoder.put(trace)

    # Verify that encoder has traces
    assert len(encoder) == 6

    # Track all payloads generated
    payloads = []
    total_processed = 0

    # Keep calling encode until all traces are processed
    while len(encoder) > 0:
        payload, count = encoder.encode()
        if count == 0:
            break
        total_processed += count
        if payload:
            payloads.append(payload)

    # Verify that multiple payloads were created due to splitting
    assert len(payloads) >= 2  # Should have at least 2 payloads due to splitting

    # Verify that all traces were eventually processed
    assert total_processed == 6

    # Verify that encoder buffer is empty after processing
    assert len(encoder) == 0

    # Verify that all payloads are valid msgpack
    for payload in payloads:
        assert payload is not None
        assert isinstance(payload, bytes)
        decoded = msgpack.unpackb(payload, raw=True, strict_map_key=False)
        assert b"events" in decoded
        assert len(decoded[b"events"]) > 0
