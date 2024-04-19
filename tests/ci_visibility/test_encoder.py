import json
import os
from unittest import mock

import msgpack
import pytest

import ddtrace
from ddtrace._trace.span import Span
from ddtrace.contrib.pytest.plugin import is_enabled
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.constants import ITR_CORRELATION_ID_TAG_NAME
from ddtrace.internal.ci_visibility.constants import SESSION_ID
from ddtrace.internal.ci_visibility.constants import SUITE_ID
from ddtrace.internal.ci_visibility.encoder import CIVisibilityCoverageEncoderV02
from ddtrace.internal.ci_visibility.encoder import CIVisibilityEncoderV01
from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
from ddtrace.internal.encoding import JSONEncoder
from tests.ci_visibility.test_ci_visibility import _dummy_noop_git_client
from tests.ci_visibility.util import _patch_dummy_writer
from tests.utils import TracerTestCase
from tests.utils import override_env


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
    encoder.set_metadata(
        {
            "language": "python",
        }
    )
    for trace in traces:
        encoder.put(trace)
    payload = encoder.encode()
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

    complete_payload = encoder.encode()
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
    complete_payload = encoder.encode()
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

    complete_payload = encoder.encode()
    assert complete_payload is None


class PytestEncodingTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch):
        self.testdir = testdir
        self.monkeypatch = monkeypatch

    def inline_run(self, *args):
        """Execute test script with test tracer."""

        class CIVisibilityPlugin:
            @staticmethod
            def pytest_configure(config):
                if is_enabled(config):
                    with _patch_dummy_writer():
                        assert CIVisibility.enabled
                        CIVisibility.disable()
                        CIVisibility.enable(tracer=self.tracer, config=ddtrace.config.pytest)

        with override_env(dict(DD_API_KEY="foobar.baz")), _dummy_noop_git_client(), mock.patch.object(
            CIVisibility,
            "_check_settings_api",
            return_value=_CIVisibilitySettings(False, False, False, False),
        ):
            return self.testdir.inline_run(*args, plugins=[CIVisibilityPlugin()])

    def subprocess_run(self, *args):
        """Execute test script with test tracer."""
        return self.testdir.runpytest_subprocess(*args)

    def teardown(self):
        if CIVisibility.enabled:
            CIVisibility.disable()

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
        event_payload = ci_agentless_encoder.encode()
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
        event_payload = ci_agentless_encoder.encode()
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
        event_payload = ci_agentless_encoder.encode()
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
        event_payload = ci_agentless_encoder.encode()
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
