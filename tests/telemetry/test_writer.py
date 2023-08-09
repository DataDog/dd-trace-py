import os
import time
from typing import Any
from typing import Dict

import httpretty
import mock
import pytest
from six import PY2

from ddtrace.internal.telemetry.data import get_application
from ddtrace.internal.telemetry.data import get_dependencies
from ddtrace.internal.telemetry.data import get_host_info
from ddtrace.internal.telemetry.writer import TelemetryWriter
from ddtrace.internal.telemetry.writer import get_runtime_id
from ddtrace.internal.utils.version import _pep440_to_semver
from ddtrace.settings import _config as config

from .conftest import TelemetryTestSession


def test_add_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that add_event queues a telemetry request with valid headers and payload"""
    payload = {"test": "123"}
    payload_type = "test-event"
    # add event to the queue
    telemetry_writer.add_event(payload, payload_type)
    # send request to the agent
    telemetry_writer.periodic()

    requests = test_agent_session.get_requests()
    assert len(requests) == 1
    assert requests[0]["headers"]["Content-Type"] == "application/json"
    assert requests[0]["headers"]["DD-Client-Library-Language"] == "python"
    assert requests[0]["headers"]["DD-Client-Library-Version"] == _pep440_to_semver()
    assert requests[0]["headers"]["DD-Telemetry-Request-Type"] == payload_type
    assert requests[0]["headers"]["DD-Telemetry-API-Version"] == "v2"
    assert requests[0]["headers"]["DD-Telemetry-Debug-Enabled"] == "False"
    assert requests[0]["body"] == _get_request_body(payload, payload_type)


def test_add_event_disabled_writer(telemetry_writer, test_agent_session):
    """asserts that add_event() does not create a telemetry request when telemetry writer is disabled"""
    telemetry_writer.disable()

    payload = {"test": "123"}
    payload_type = "test-event"
    # ensure events are not queued when telemetry is disabled
    telemetry_writer.add_event(payload, payload_type)

    # ensure no request were sent
    telemetry_writer.periodic()
    assert len(test_agent_session.get_requests()) == 0


def test_app_started_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that _app_started_event() queues a valid telemetry request which is then sent by periodic()"""
    # queue an app started event
    telemetry_writer._app_started_event()
    # force a flush
    telemetry_writer.periodic()

    requests = test_agent_session.get_requests()
    assert len(requests) == 1
    assert requests[0]["headers"]["DD-Telemetry-Request-Type"] == "app-started"

    events = test_agent_session.get_events()
    assert len(events) == 1

    events[0]["payload"]["configuration"].sort(key=lambda c: c["name"])
    payload = {
        "configuration": [
            {"name": "DD_APPSEC_ENABLED", "origin": "unknown", "value": False},
            {"name": "DD_CALL_BASIC_CONFIG", "origin": "unknown", "value": False},
            {"name": "DD_DATA_STREAMS_ENABLED", "origin": "unknown", "value": False},
            {"name": "DD_DYNAMIC_INSTRUMENTATION_ENABLED", "origin": "unknown", "value": False},
            {"name": "DD_EXCEPTION_DEBUGGING_ENABLED", "origin": "unknown", "value": False},
            {"name": "DD_INSTRUMENTATION_TELEMETRY_ENABLED", "origin": "unknown", "value": True},
            {"name": "DD_LOGS_INJECTION", "origin": "unknown", "value": False},
            {"name": "DD_PROFILING_ENABLED", "origin": "unknown", "value": False},
            {"name": "DD_RUNTIME_METRICS_ENABLED", "origin": "unknown", "value": False},
            {"name": "DD_SPAN_SAMPLING_RULES", "origin": "unknown", "value": None},
            {"name": "DD_SPAN_SAMPLING_RULES_FILE", "origin": "unknown", "value": None},
            {"name": "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", "origin": "unknown", "value": False},
            {"name": "DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED", "origin": "unknown", "value": False},
            {"name": "DD_TRACE_ANALYTICS_ENABLED", "origin": "unknown", "value": False},
            {"name": "DD_TRACE_CLIENT_IP_ENABLED", "origin": "unknown", "value": None},
            {"name": "DD_TRACE_COMPUTE_STATS", "origin": "unknown", "value": False},
            {"name": "DD_TRACE_DEBUG", "origin": "unknown", "value": False},
            {"name": "DD_TRACE_ENABLED", "origin": "unknown", "value": True},
            {"name": "DD_TRACE_HEALTH_METRICS_ENABLED", "origin": "unknown", "value": False},
            {
                "name": "DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN",
                "origin": "unknown",
                "value": "(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?"
                "|public_?|access_?|secret_?)key(?:_?id)?|token|consumer_?(?:id|key|secret)|sign"
                '(?:ed|ature)?|auth(?:entication|orization)?)(?:(?:\\s|%20)*(?:=|%3D)[^&]+|(?:"|'
                '%22)(?:\\s|%20)*(?::|%3A)(?:\\s|%20)*(?:"|%22)(?:%2[^2]|%[^2]|[^"%])+(?:"|%22))|'
                "bearer(?:\\s|%20)+[a-z0-9\\._\\-]|token(?::|%3A)[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}|"
                "ey[I-L](?:[\\w=-]|%3D)+\\.ey[I-L](?:[\\w=-]|%3D)+(?:\\.(?:[\\w.+\\/=-]|%3D|%2F|%2B)+)?|["
                "\\-]{5}BEGIN(?:[a-z\\s]|%20)+PRIVATE(?:\\s|%20)KEY[\\-]{5}[^\\-]+[\\-]{5}END(?:[a-z\\s]|%"
                "20)+PRIVATE(?:\\s|%20)KEY|ssh-rsa(?:\\s|%20)*(?:[a-z0-9\\/\\.+]|%2F|%5C|%2B){100,}",
            },
            {"name": "DD_TRACE_OTEL_ENABLED", "origin": "unknown", "value": False},
            {"name": "DD_TRACE_PROPAGATION_STYLE_EXTRACT", "origin": "unknown", "value": "tracecontext,datadog"},
            {"name": "DD_TRACE_PROPAGATION_STYLE_INJECT", "origin": "unknown", "value": "tracecontext,datadog"},
            {"name": "DD_TRACE_RATE_LIMIT", "origin": "unknown", "value": 100},
            {"name": "DD_TRACE_SAMPLE_RATE", "origin": "unknown", "value": None},
            {"name": "ddtrace_auto_used", "origin": "unknown", "value": False},
            {"name": "ddtrace_bootstrapped", "origin": "unknown", "value": False},
        ],
        "error": {
            "code": 0,
            "message": "",
        },
    }
    assert events[0] == _get_request_body(payload, "app-started")


def test_app_started_event_configuration_override(test_agent_session, run_python_code_in_subprocess, tmpdir):
    """
    asserts that default configuration value
    is changed and queues a valid telemetry request
    which is then sent by periodic()
    """
    code = """
import logging
logging.basicConfig()

import ddtrace.auto

from ddtrace.internal.telemetry import telemetry_writer
telemetry_writer.enable()
telemetry_writer.reset_queues()
telemetry_writer._app_started_event()
telemetry_writer.periodic()
telemetry_writer.disable()
    """

    env = os.environ.copy()
    # Change configuration default values
    env["DD_EXCEPTION_DEBUGGING_ENABLED"] = "True"
    env["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "True"
    env["DD_LOGS_INJECTION"] = "True"
    env["DD_CALL_BASIC_CONFIG"] = "True"
    env["DD_PROFILING_ENABLED"] = "True"
    env["DD_RUNTIME_METRICS_ENABLED"] = "True"
    env["DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED"] = "True"
    env["DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED"] = "True"
    env["DD_TRACE_ANALYTICS_ENABLED"] = "True"
    env["DD_TRACE_CLIENT_IP_ENABLED"] = "True"
    env["DD_TRACE_COMPUTE_STATS"] = "True"
    env["DD_TRACE_DEBUG"] = "True"
    env["DD_TRACE_ENABLED"] = "False"
    env["DD_TRACE_HEALTH_METRICS_ENABLED"] = "True"
    env["DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN"] = ".*"
    env["DD_TRACE_OTEL_ENABLED"] = "True"
    env["DD_TRACE_PROPAGATION_STYLE_EXTRACT"] = "tracecontext"
    env["DD_TRACE_PROPAGATION_STYLE_INJECT"] = "tracecontext"
    env["DD_TRACE_SAMPLE_RATE"] = "0.5"
    env["DD_TRACE_RATE_LIMIT"] = "50"

    if PY2:
        # Prevents gevent importerror when profiling is enabled
        env["DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE"] = "false"

    file = tmpdir.join("moon_ears.json")
    file.write('[{"service":"xy?","name":"a*c"}]')
    env["DD_SPAN_SAMPLING_RULES"] = '[{"service":"xyz", "sample_rate":0.23}]'
    env["DD_SPAN_SAMPLING_RULES_FILE"] = str(file)

    _, stderr, status, _ = run_python_code_in_subprocess(code, env=env)

    assert status == 0, stderr

    events = test_agent_session.get_events()
    events[0]["payload"]["configuration"].sort(key=lambda c: c["name"])

    assert events[0]["payload"]["configuration"] == [
        {"name": "DD_APPSEC_ENABLED", "origin": "unknown", "value": False},
        {"name": "DD_CALL_BASIC_CONFIG", "origin": "unknown", "value": True},
        {"name": "DD_DATA_STREAMS_ENABLED", "origin": "unknown", "value": False},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_ENABLED", "origin": "unknown", "value": False},
        {"name": "DD_EXCEPTION_DEBUGGING_ENABLED", "origin": "unknown", "value": True},
        {"name": "DD_INSTRUMENTATION_TELEMETRY_ENABLED", "origin": "unknown", "value": True},
        {"name": "DD_LOGS_INJECTION", "origin": "unknown", "value": True},
        {"name": "DD_PROFILING_ENABLED", "origin": "unknown", "value": True},
        {"name": "DD_RUNTIME_METRICS_ENABLED", "origin": "unknown", "value": True},
        {"name": "DD_SPAN_SAMPLING_RULES", "origin": "unknown", "value": '[{"service":"xyz", "sample_rate":0.23}]'},
        {"name": "DD_SPAN_SAMPLING_RULES_FILE", "origin": "unknown", "value": str(file)},
        {"name": "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", "origin": "unknown", "value": True},
        {"name": "DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED", "origin": "unknown", "value": True},
        {"name": "DD_TRACE_ANALYTICS_ENABLED", "origin": "unknown", "value": True},
        {"name": "DD_TRACE_CLIENT_IP_ENABLED", "origin": "unknown", "value": None},
        {"name": "DD_TRACE_COMPUTE_STATS", "origin": "unknown", "value": True},
        {"name": "DD_TRACE_DEBUG", "origin": "unknown", "value": True},
        {"name": "DD_TRACE_ENABLED", "origin": "unknown", "value": False},
        {"name": "DD_TRACE_HEALTH_METRICS_ENABLED", "origin": "unknown", "value": True},
        {"name": "DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN", "origin": "unknown", "value": ".*"},
        {"name": "DD_TRACE_OTEL_ENABLED", "origin": "unknown", "value": True},
        {"name": "DD_TRACE_PROPAGATION_STYLE_EXTRACT", "origin": "unknown", "value": "tracecontext"},
        {"name": "DD_TRACE_PROPAGATION_STYLE_INJECT", "origin": "unknown", "value": "tracecontext"},
        {"name": "DD_TRACE_RATE_LIMIT", "origin": "unknown", "value": 50},
        {"name": "DD_TRACE_SAMPLE_RATE", "origin": "unknown", "value": "0.5"},
        {"name": "ddtrace_auto_used", "origin": "unknown", "value": True},
        {"name": "ddtrace_bootstrapped", "origin": "unknown", "value": True},
    ]


def test_app_dependencies_loaded_event(telemetry_writer, test_agent_session, mock_time):
    telemetry_writer._app_dependencies_loaded_event()
    # force a flush
    telemetry_writer.periodic()
    events = test_agent_session.get_events()
    assert len(events) == 1
    payload = {"dependencies": get_dependencies()}
    assert events[0] == _get_request_body(payload, "app-dependencies-loaded")


def test_app_closing_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that app_shutdown() queues and sends an app-closing telemetry request"""
    # send app closed event
    telemetry_writer.app_shutdown()

    requests = test_agent_session.get_requests()
    assert len(requests) == 1
    assert requests[0]["headers"]["DD-Telemetry-Request-Type"] == "app-closing"
    # ensure a valid request body was sent
    assert requests[0]["body"] == _get_request_body({}, "app-closing")


def test_add_integration(telemetry_writer, test_agent_session, mock_time):
    """asserts that add_integration() queues a valid telemetry request"""
    # queue integrations
    telemetry_writer.add_integration("integration-t", True, True, "")
    telemetry_writer.add_integration("integration-f", False, False, "terrible failure")
    # send integrations to the agent
    telemetry_writer.periodic()

    requests = test_agent_session.get_requests()
    assert len(requests) == 1

    # assert integration change telemetry request was sent
    assert requests[0]["headers"]["DD-Telemetry-Request-Type"] == "app-integrations-change"
    # assert that the request had a valid request body
    requests[0]["body"]["payload"]["integrations"].sort(key=lambda x: x["name"])
    expected_payload = {
        "integrations": [
            {
                "name": "integration-f",
                "version": "",
                "enabled": False,
                "auto_enabled": False,
                "compatible": False,
                "error": "terrible failure",
            },
            {
                "name": "integration-t",
                "version": "",
                "enabled": True,
                "auto_enabled": True,
                "compatible": True,
                "error": "",
            },
        ]
    }
    assert requests[0]["body"] == _get_request_body(expected_payload, "app-integrations-change")


def test_app_client_configuration_changed_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that queuing a configuration sends a valid telemetry request"""

    telemetry_writer.add_configuration("appsec_enabled", True)
    telemetry_writer.add_configuration("DD_TRACE_PROPAGATION_STYLE_EXTRACT", "datadog")
    telemetry_writer.add_configuration("appsec_enabled", False, "env_var")

    telemetry_writer.periodic()

    events = test_agent_session.get_events()
    assert len(events) == 1
    assert events[0]["request_type"] == "app-client-configuration-change"
    received_configurations = events[0]["payload"]["configuration"]
    # Sort the configuration list by name
    received_configurations.sort(key=lambda c: c["name"])

    # assert the latest configuration value is send to the agent
    assert received_configurations == [
        {
            "name": "DD_TRACE_PROPAGATION_STYLE_EXTRACT",
            "origin": "unknown",
            "value": "datadog",
        },
        {
            "name": "appsec_enabled",
            "origin": "env_var",
            "value": False,
        },
    ]


def test_add_integration_disabled_writer(telemetry_writer, test_agent_session):
    """asserts that add_integration() does not queue an integration when telemetry is disabled"""
    telemetry_writer.disable()

    telemetry_writer.add_integration("integration-name", True, False, "")
    telemetry_writer.periodic()

    assert len(test_agent_session.get_requests()) == 0


@pytest.mark.parametrize("mock_status", [300, 400, 401, 403, 500])
def test_send_failing_request(mock_status, telemetry_writer):
    """asserts that a warning is logged when an unsuccessful response is returned by the http client"""

    with httpretty.enabled():
        httpretty.register_uri(httpretty.POST, telemetry_writer._client.url, status=mock_status)
        with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
            # sends failing app-closing event
            telemetry_writer.app_shutdown()
            # asserts unsuccessful status code was logged
            log.debug.assert_called_with(
                "failed to send telemetry to the Datadog Agent at %s. response: %s",
                telemetry_writer._client.url,
                mock_status,
            )
        # ensure one failing request was sent
        assert len(httpretty.latest_requests()) == 1


@pytest.mark.parametrize("telemetry_writer", [TelemetryWriter()])
def test_telemetry_graceful_shutdown(telemetry_writer, test_agent_session, mock_time):
    telemetry_writer.start()
    telemetry_writer.stop()
    # mocks calling sys.atexit hooks
    telemetry_writer.app_shutdown()

    events = test_agent_session.get_events()
    assert len(events) == 3

    # Reverse chronological order
    assert events[0]["request_type"] == "app-closing"
    assert events[0] == _get_request_body({}, "app-closing", 3)
    assert events[1]["request_type"] == "app-dependencies-loaded"
    assert events[2]["request_type"] == "app-started"


def test_app_heartbeat_event_periodic(mock_time, telemetry_writer, test_agent_session):
    # type: (mock.Mock, Any, TelemetryWriter) -> None
    """asserts that we queue/send app-heartbeat when periodc() is called"""

    # Assert default hearbeat interval is 60 seconds
    assert telemetry_writer.interval == 60
    # Assert next flush contains app-heartbeat event
    telemetry_writer.periodic()
    _assert_app_heartbeat_event(1, test_agent_session)


def test_app_heartbeat_event(mock_time, telemetry_writer, test_agent_session):
    # type: (mock.Mock, Any, TelemetryWriter) -> None
    """asserts that we queue/send app-heartbeat event every 60 seconds when app_heartbeat_event() is called"""

    # Assert clean slate
    events = test_agent_session.get_events()
    assert len(events) == 0

    # Assert a maximum of one heartbeat is queued per flush
    telemetry_writer._app_heartbeat_event()
    telemetry_writer.periodic()
    events = test_agent_session.get_events()
    assert len(events) == 1


def _assert_app_heartbeat_event(seq_id, test_agent_session):
    # type: (int, TelemetryTestSession) -> None
    """used to test heartbeat events received by the testagent"""
    events = test_agent_session.get_events()
    assert len(events) == seq_id
    # The test_agent returns telemetry events in reverse chronological order
    # The first event in the list is last event sent by the Telemetry Client
    last_event = events[0]
    assert last_event["request_type"] == "app-heartbeat"
    assert last_event == _get_request_body({}, "app-heartbeat", seq_id=seq_id)


def _get_request_body(payload, payload_type, seq_id=1):
    # type: (Dict, str, int) -> Dict
    """used to test the body of requests received by the testagent"""
    return {
        "tracer_time": time.time(),
        "runtime_id": get_runtime_id(),
        "api_version": "v2",
        "debug": False,
        "seq_id": seq_id,
        "application": get_application(config.service, config.version, config.env),
        "host": get_host_info(),
        "payload": payload,
        "request_type": payload_type,
    }
