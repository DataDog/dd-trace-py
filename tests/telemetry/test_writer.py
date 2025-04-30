import os
import sys
import sysconfig
import time
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401

import httpretty
import mock
import pytest

from ddtrace import config
import ddtrace.internal.telemetry
from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT
from ddtrace.internal.telemetry.data import get_application
from ddtrace.internal.telemetry.data import get_host_info
from ddtrace.internal.telemetry.writer import get_runtime_id
from ddtrace.internal.utils.version import _pep440_to_semver
from ddtrace.settings._config import DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP_DEFAULT
from tests.conftest import DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME
from tests.utils import call_program
from tests.utils import override_global_config


def test_add_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that add_event queues a telemetry request with valid headers and payload"""
    payload = {"test": "123"}
    payload_type = "test-event"
    # add event to the queue
    telemetry_writer.add_event(payload, payload_type)
    # send request to the agent
    telemetry_writer.periodic(force_flush=True)

    requests = test_agent_session.get_requests(payload_type)
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
    payload = {"test": "123"}
    payload_type = "test-event"
    # ensure events are not queued when telemetry is disabled
    telemetry_writer.add_event(payload, payload_type)

    # ensure no request were sent
    telemetry_writer.periodic(force_flush=True)
    assert len(test_agent_session.get_requests(payload_type)) == 1


@pytest.mark.parametrize(
    "env_var,value,expected_value",
    [
        ("DD_APPSEC_SCA_ENABLED", "true", True),
        ("DD_APPSEC_SCA_ENABLED", "True", True),
        ("DD_APPSEC_SCA_ENABLED", "1", True),
        ("DD_APPSEC_SCA_ENABLED", "false", False),
        ("DD_APPSEC_SCA_ENABLED", "False", False),
        ("DD_APPSEC_SCA_ENABLED", "0", False),
    ],
)
def test_app_started_event_configuration_override_asm(
    test_agent_session, run_python_code_in_subprocess, env_var, value, expected_value
):
    """asserts that asm configuration value is changed and queues a valid telemetry request"""
    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"
    env["DD_APPSEC_ENABLED"] = "true"
    env[env_var] = value
    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace.auto", env=env)
    assert status == 0, stderr

    configuration = test_agent_session.get_configurations(name=env_var)
    assert len(configuration) == 1, configuration
    assert configuration[0] == {"name": env_var, "origin": "env_var", "value": expected_value}


def test_app_started_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that app_started() queues a valid telemetry request which is then sent by periodic()"""
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        # queue an app started event
        telemetry_writer._app_started()
        # force a flush
        telemetry_writer.periodic(force_flush=True)

        requests = test_agent_session.get_requests("app-started")
        assert len(requests) == 1
        assert requests[0]["headers"]["DD-Telemetry-Request-Type"] == "app-started"

        payload = {
            "configuration": sorted(
                [
                    {"name": "DD_AGENT_HOST", "origin": "unknown", "value": None},
                    {"name": "DD_AGENT_PORT", "origin": "unknown", "value": None},
                    {"name": "DD_DOGSTATSD_PORT", "origin": "unknown", "value": None},
                    {"name": "DD_DOGSTATSD_URL", "origin": "unknown", "value": None},
                    {"name": "DD_DYNAMIC_INSTRUMENTATION_ENABLED", "origin": "unknown", "value": False},
                    {"name": "DD_EXCEPTION_REPLAY_ENABLED", "origin": "unknown", "value": False},
                    {"name": "DD_INSTRUMENTATION_TELEMETRY_ENABLED", "origin": "unknown", "value": True},
                    {"name": "DD_PROFILING_STACK_ENABLED", "origin": "unknown", "value": True},
                    {"name": "DD_PROFILING_MEMORY_ENABLED", "origin": "unknown", "value": True},
                    {"name": "DD_PROFILING_HEAP_ENABLED", "origin": "unknown", "value": True},
                    {"name": "DD_PROFILING_LOCK_ENABLED", "origin": "unknown", "value": True},
                    {"name": "DD_PROFILING_EXPORT_LIBDD_ENABLED", "origin": "unknown", "value": False},
                    {"name": "DD_PROFILING_CAPTURE_PCT", "origin": "unknown", "value": 1.0},
                    {"name": "DD_PROFILING_UPLOAD_INTERVAL", "origin": "unknown", "value": 60.0},
                    {"name": "DD_PROFILING_MAX_FRAMES", "origin": "unknown", "value": 64},
                    {"name": "DD_REMOTE_CONFIGURATION_ENABLED", "origin": "unknown", "value": False},
                    {"name": "DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS", "origin": "unknown", "value": 5.0},
                    {"name": "DD_RUNTIME_METRICS_ENABLED", "origin": "unknown", "value": True},
                    {"name": "DD_SERVICE_MAPPING", "origin": "unknown", "value": ""},
                    {"name": "DD_SPAN_SAMPLING_RULES", "origin": "unknown", "value": None},
                    {"name": "DD_SPAN_SAMPLING_RULES_FILE", "origin": "unknown", "value": None},
                    {"name": "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", "origin": "unknown", "value": True},
                    {"name": "DD_TRACE_AGENT_HOSTNAME", "origin": "default", "value": None},
                    {"name": "DD_TRACE_AGENT_PORT", "origin": "default", "value": None},
                    {"name": "DD_TRACE_AGENT_TIMEOUT_SECONDS", "origin": "unknown", "value": 2.0},
                    {"name": "DD_TRACE_API_VERSION", "origin": "unknown", "value": None},
                    {"name": "DD_TRACE_CLIENT_IP_ENABLED", "origin": "unknown", "value": None},
                    {"name": "DD_TRACE_COMPUTE_STATS", "origin": "unknown", "value": False},
                    {"name": "DD_TRACE_DEBUG", "origin": "unknown", "value": False},
                    {"name": "DD_TRACE_HEALTH_METRICS_ENABLED", "origin": "unknown", "value": False},
                    {
                        "name": "DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP",
                        "origin": "unknown",
                        "value": DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP_DEFAULT,
                    },
                    {"name": "DD_TRACE_OTEL_ENABLED", "origin": "unknown", "value": False},
                    {"name": "DD_TRACE_PARTIAL_FLUSH_ENABLED", "origin": "unknown", "value": True},
                    {"name": "DD_TRACE_PARTIAL_FLUSH_MIN_SPANS", "origin": "unknown", "value": 300},
                    {
                        "name": "DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED",
                        "origin": "default",
                        "value": False,
                    },
                    {
                        "name": "DD_TRACE_PEER_SERVICE_MAPPING",
                        "origin": "env_var",
                        "value": "default_service:remapped_service",
                    },
                    {"name": "DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED", "origin": "unknown", "value": False},
                    {"name": "DD_TRACE_PEER_SERVICE_MAPPING", "origin": "unknown", "value": ""},
                    {
                        "name": "DD_TRACE_PROPAGATION_STYLE_EXTRACT",
                        "origin": "unknown",
                        "value": "datadog,tracecontext",
                    },
                    {"name": "DD_TRACE_PROPAGATION_STYLE_INJECT", "origin": "unknown", "value": "datadog,tracecontext"},
                    {"name": "DD_TRACE_RATE_LIMIT", "origin": "unknown", "value": 100},
                    {"name": "DD_TRACE_REMOVE_INTEGRATION_SERVICE_NAMES_ENABLED", "origin": "unknown", "value": False},
                    {"name": "DD_TRACE_SPAN_ATTRIBUTE_SCHEMA", "origin": "unknown", "value": "v0"},
                    {"name": "DD_TRACE_STARTUP_LOGS", "origin": "unknown", "value": False},
                    {"name": "DD_TRACE_WRITER_BUFFER_SIZE_BYTES", "origin": "unknown", "value": 20 << 20},
                    {"name": "DD_TRACE_WRITER_INTERVAL_SECONDS", "origin": "unknown", "value": 1.0},
                    {"name": "DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES", "origin": "unknown", "value": 20 << 20},
                    {"name": "DD_TRACE_WRITER_REUSE_CONNECTIONS", "origin": "unknown", "value": False},
                    {"name": "instrumentation_source", "origin": "code", "value": "manual"},
                    {"name": "profiling_enabled", "origin": "default", "value": "false"},
                    {"name": "data_streams_enabled", "origin": "default", "value": "false"},
                    {"name": "appsec_enabled", "origin": "default", "value": "false"},
                    {"name": "crashtracking_create_alt_stack", "origin": "unknown", "value": True},
                    {"name": "crashtracking_use_alt_stack", "origin": "unknown", "value": True},
                    {"name": "crashtracking_available", "origin": "unknown", "value": sys.platform == "linux"},
                    {"name": "crashtracking_debug_url", "origin": "unknown", "value": None},
                    {"name": "crashtracking_enabled", "origin": "unknown", "value": sys.platform == "linux"},
                    {"name": "crashtracking_stacktrace_resolver", "origin": "unknown", "value": "full"},
                    {"name": "crashtracking_started", "origin": "unknown", "value": False},
                    {"name": "crashtracking_stderr_filename", "origin": "unknown", "value": None},
                    {"name": "crashtracking_stdout_filename", "origin": "unknown", "value": None},
                    {
                        "name": "python_build_gnu_type",
                        "origin": "unknown",
                        "value": sysconfig.get_config_var("BUILD_GNU_TYPE"),
                    },
                    {
                        "name": "python_host_gnu_type",
                        "origin": "unknown",
                        "value": sysconfig.get_config_var("HOST_GNU_TYPE"),
                    },
                    {"name": "python_soabi", "origin": "unknown", "value": sysconfig.get_config_var("SOABI")},
                    {"name": "trace_sample_rate", "origin": "default", "value": "1.0"},
                    {"name": "trace_sampling_rules", "origin": "default", "value": ""},
                    {"name": "trace_header_tags", "origin": "default", "value": ""},
                    {"name": "logs_injection_enabled", "origin": "default", "value": "false"},
                    {"name": "trace_tags", "origin": "default", "value": ""},
                    {"name": "trace_enabled", "origin": "default", "value": "true"},
                    {"name": "instrumentation_config_id", "origin": "default", "value": ""},
                    {"name": "DD_INJECT_FORCE", "origin": "unknown", "value": True},
                    {"name": "DD_LIB_INJECTED", "origin": "unknown", "value": False},
                    {"name": "DD_LIB_INJECTION_ATTEMPTED", "origin": "unknown", "value": False},
                ],
                key=lambda x: x["name"],
            ),
            "error": {
                "code": 0,
                "message": "",
            },
        }
        requests[0]["body"]["payload"]["configuration"].sort(key=lambda c: c["name"])
        result = _get_request_body(payload, "app-started")
        result["payload"]["configuration"] = [
            a for a in result["payload"]["configuration"] if a["name"] != "DD_TRACE_AGENT_URL"
        ]
        assert payload == result["payload"]


def test_app_started_event_configuration_override(test_agent_session, run_python_code_in_subprocess, tmpdir):
    """
    asserts that default configuration value
    is changed and queues a valid telemetry request
    which is then sent by periodic()
    """
    code = """
# most configurations are reported when ddtrace.auto is imported
import ddtrace.auto
# report configurations not used by ddtrace.auto
import ddtrace.settings.symbol_db
import ddtrace.settings.dynamic_instrumentation
import ddtrace.settings.exception_replay
    """

    env = os.environ.copy()
    # Change configuration default values
    env["DD_EXCEPTION_REPLAY_ENABLED"] = "True"
    env["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "True"
    env["DD_TRACE_STARTUP_LOGS"] = "True"
    env["DD_LOGS_INJECTION"] = "True"
    env["DD_DATA_STREAMS_ENABLED"] = "true"
    env["DD_APPSEC_ENABLED"] = "False"
    env["DD_RUNTIME_METRICS_ENABLED"] = "True"
    env["DD_SERVICE_MAPPING"] = "default_dd_service:remapped_dd_service"
    env["DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED"] = "True"
    env["DD_TRACE_CLIENT_IP_ENABLED"] = "True"
    env["DD_TRACE_COMPUTE_STATS"] = "True"
    env["DD_TRACE_DEBUG"] = "True"
    env["DD_TRACE_ENABLED"] = "False"
    env["DD_TRACE_HEALTH_METRICS_ENABLED"] = "True"
    env["DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP"] = ".*"
    env["DD_TRACE_OTEL_ENABLED"] = "True"
    env["DD_TRACE_PROPAGATION_STYLE_EXTRACT"] = "tracecontext"
    env["DD_TRACE_PROPAGATION_STYLE_INJECT"] = "tracecontext"
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = "True"
    env["DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS"] = "1"
    env["DD_TRACE_RATE_LIMIT"] = "50"
    env["DD_TRACE_SAMPLING_RULES"] = '[{"sample_rate":1.0,"service":"xyz","name":"abc"}]'
    env["DD_PROFILING_ENABLED"] = "True"
    env["DD_PROFILING_STACK_ENABLED"] = "False"
    env["DD_PROFILING_MEMORY_ENABLED"] = "False"
    env["DD_PROFILING_HEAP_ENABLED"] = "False"
    env["DD_PROFILING_LOCK_ENABLED"] = "False"
    # FIXME: Profiling native exporter can be enabled even if DD_PROFILING_EXPORT_LIBDD_ENABLED=False. The native
    # exporter will be always be enabled stack v2 is enabled and the ddup module is available (platform dependent).
    # env["DD_PROFILING_EXPORT_LIBDD_ENABLED"] = "False"
    env["DD_PROFILING_CAPTURE_PCT"] = "5.0"
    env["DD_PROFILING_UPLOAD_INTERVAL"] = "10.0"
    env["DD_PROFILING_MAX_FRAMES"] = "512"
    env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = "v1"
    env["DD_TRACE_REMOVE_INTEGRATION_SERVICE_NAMES_ENABLED"] = "True"
    env["DD_TRACE_PEER_SERVICE_MAPPING"] = "default_service:remapped_service"
    env["DD_TRACE_API_VERSION"] = "v0.5"
    env["DD_TRACE_WRITER_BUFFER_SIZE_BYTES"] = "1000"
    env["DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES"] = "9999"
    env["DD_TRACE_WRITER_INTERVAL_SECONDS"] = "30"
    env["DD_TRACE_WRITER_REUSE_CONNECTIONS"] = "True"
    env["DD_TAGS"] = "team:apm,component:web"
    env["DD_INSTRUMENTATION_CONFIG_ID"] = "abcedf123"

    file = tmpdir.join("moon_ears.json")
    file.write('[{"service":"xy?","name":"a*c"}]')
    env["DD_SPAN_SAMPLING_RULES"] = '[{"service":"xyz", "sample_rate":0.23}]'
    env["DD_SPAN_SAMPLING_RULES_FILE"] = str(file)
    env["DD_TRACE_PARTIAL_FLUSH_ENABLED"] = "false"
    env["DD_TRACE_PARTIAL_FLUSH_MIN_SPANS"] = "3"
    env["DD_TRACE_PROPAGATION_BEHAVIOR_EXTRACT"] = "restart"
    env["DD_SITE"] = "datadoghq.com"
    env["DD_APPSEC_RASP_ENABLED"] = "False"
    env["DD_API_SECURITY_ENABLED"] = "False"
    env["DD_APPSEC_AUTOMATED_USER_EVENTS_TRACKING_ENABLED"] = "False"
    env["DD_APPSEC_AUTO_USER_INSTRUMENTATION_MODE"] = "disabled"
    env["DD_INJECT_FORCE"] = "true"
    env["DD_INJECTION_ENABLED"] = "true"

    # By default telemetry collection is enabled after 10 seconds, so we either need to
    # to sleep for 10 seconds or manually call _app_started() to generate the app started event.
    # This delay allows us to collect start up errors and dynamic configurations
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    # DD_TRACE_AGENT_URL in gitlab is different from CI, to keep things simple we will
    # skip validating this config
    configurations = test_agent_session.get_configurations(ignores=["DD_TRACE_AGENT_URL"])
    assert configurations

    expected = [
        {"name": "DD_AGENT_HOST", "origin": "default", "value": None},
        {"name": "DD_AGENT_PORT", "origin": "default", "value": None},
        {"name": "DD_API_KEY", "origin": "default", "value": None},
        {"name": "DD_API_SECURITY_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_API_SECURITY_PARSE_RESPONSE_BODY", "origin": "default", "value": True},
        {"name": "DD_API_SECURITY_SAMPLE_DELAY", "origin": "default", "value": 30.0},
        {"name": "DD_APM_TRACING_ENABLED", "origin": "default", "value": True},
        {"name": "DD_APPSEC_AUTOMATED_USER_EVENTS_TRACKING_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_APPSEC_AUTO_USER_INSTRUMENTATION_MODE", "origin": "env_var", "value": "disabled"},
        {"name": "DD_APPSEC_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_APPSEC_MAX_STACK_TRACES", "origin": "default", "value": 2},
        {"name": "DD_APPSEC_MAX_STACK_TRACE_DEPTH", "origin": "default", "value": 32},
        {"name": "DD_APPSEC_MAX_STACK_TRACE_DEPTH_TOP_PERCENT", "origin": "default", "value": 75.0},
        {
            "name": "DD_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP",
            "origin": "default",
            "value": "(?i)pass|pw(?:or)?d|secret|(?:api|private|public|access)[_-]?key|token|consumer"
            "[_-]?(?:id|key|secret)|sign(?:ed|ature)|bearer|authorization|jsessionid|phpsessid|asp\\"
            ".net[_-]sessionid|sid|jwt",
        },
        {
            "name": "DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP",
            "origin": "default",
            "value": "(?i)(?:p(?:ass)?w(?:or)?d|pass(?:[_-]?phrase)?|secret(?:[_-]?key)?|(?:(?:api|private"
            "|public|access)[_-]?)key(?:[_-]?id)?|(?:(?:auth|access|id|refresh)[_-]?)?token|consumer[_-]?"
            "(?:id|key|secret)|sign(?:ed|ature)?|auth(?:entication|orization)?|jsessionid|phpsessid|asp\\"
            '.net(?:[_-]|-)sessionid|sid|jwt)(?:\\s*=[^;]|"\\s*:\\s*"[^"]+")|bearer\\s+[a-z0-9\\._\\-]+|token:'
            "[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}|ey[I-L][\\w=-]+\\.ey[I-L][\\w=-]+(?:\\.[\\w.+\\/=-]+)?|[\\-]"
            "{5}BEGIN[a-z\\s]+PRIVATE\\sKEY[\\-]{5}[^\\-]+[\\-]{5}END[a-z\\s]+PRIVATE\\sKEY|ssh-rsa\\s*[a-z0-9\\/\\.+]{100,}",
        },
        {"name": "DD_APPSEC_RASP_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_APPSEC_RULES", "origin": "default", "value": None},
        {
            "name": "DD_APPSEC_SCA_ENABLED",
            "origin": "default",
            "value": None,
        },
        {"name": "DD_APPSEC_STACK_TRACE_ENABLED", "origin": "default", "value": True},
        {"name": "DD_APPSEC_WAF_TIMEOUT", "origin": "default", "value": 5.0},
        {"name": "DD_CIVISIBILITY_AGENTLESS_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_CIVISIBILITY_AGENTLESS_URL", "origin": "default", "value": ""},
        {"name": "DD_CIVISIBILITY_EARLY_FLAKE_DETECTION_ENABLED", "origin": "default", "value": True},
        {"name": "DD_CIVISIBILITY_ITR_ENABLED", "origin": "default", "value": True},
        {"name": "DD_CIVISIBILITY_LOG_LEVEL", "origin": "default", "value": "info"},
        {"name": "DD_CODE_ORIGIN_FOR_SPANS_ENABLED", "origin": "default", "value": False},
        {"name": "DD_CRASHTRACKING_CREATE_ALT_STACK", "origin": "default", "value": True},
        {"name": "DD_CRASHTRACKING_DEBUG_URL", "origin": "default", "value": None},
        {"name": "DD_CRASHTRACKING_ENABLED", "origin": "default", "value": True},
        {"name": "DD_CRASHTRACKING_STACKTRACE_RESOLVER", "origin": "default", "value": "full"},
        {"name": "DD_CRASHTRACKING_STDERR_FILENAME", "origin": "default", "value": None},
        {"name": "DD_CRASHTRACKING_STDOUT_FILENAME", "origin": "default", "value": None},
        {"name": "DD_CRASHTRACKING_TAGS", "origin": "default", "value": ""},
        {"name": "DD_CRASHTRACKING_USE_ALT_STACK", "origin": "default", "value": True},
        {"name": "DD_CRASHTRACKING_WAIT_FOR_RECEIVER", "origin": "default", "value": True},
        {"name": "DD_DATA_STREAMS_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_DJANGO_INCLUDE_USER_EMAIL", "origin": "default", "value": False},
        {"name": "DD_DJANGO_INCLUDE_USER_LOGIN", "origin": "default", "value": True},
        {"name": "DD_DJANGO_INCLUDE_USER_NAME", "origin": "default", "value": True},
        {"name": "DD_DJANGO_INCLUDE_USER_REALNAME", "origin": "default", "value": False},
        {"name": "DD_DOGSTATSD_HOST", "origin": "default", "value": None},
        {"name": "DD_DOGSTATSD_PORT", "origin": "default", "value": None},
        {"name": "DD_DOGSTATSD_URL", "origin": "default", "value": None},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_DIAGNOSTICS_INTERVAL", "origin": "default", "value": 3600},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_ENABLED", "origin": "default", "value": False},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_MAX_PAYLOAD_SIZE", "origin": "default", "value": 1048576},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_METRICS_ENABLED", "origin": "default", "value": True},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_REDACTED_IDENTIFIERS", "origin": "default", "value": "set()"},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_REDACTED_TYPES", "origin": "default", "value": "set()"},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_UPLOAD_FLUSH_INTERVAL", "origin": "default", "value": 1.0},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_UPLOAD_TIMEOUT", "origin": "default", "value": 30},
        {"name": "DD_ENV", "origin": "default", "value": None},
        {"name": "DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_AFTER_UNHANDLED", "origin": "default", "value": False},
        {"name": "DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED", "origin": "default", "value": ""},
        {"name": "DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED_MODULES", "origin": "default", "value": ""},
        {"name": "DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_LOGGER", "origin": "default", "value": ""},
        {"name": "DD_EXCEPTION_REPLAY_CAPTURE_MAX_FRAMES", "origin": "default", "value": 8},
        {"name": "DD_EXCEPTION_REPLAY_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_IAST_DEDUPLICATION_ENABLED", "origin": "default", "value": True},
        {"name": "DD_IAST_ENABLED", "origin": "default", "value": False},
        {"name": "DD_IAST_MAX_CONCURRENT_REQUESTS", "origin": "default", "value": 2},
        {"name": "DD_IAST_REDACTION_ENABLED", "origin": "default", "value": True},
        {
            "name": "DD_IAST_REDACTION_NAME_PATTERN",
            "origin": "default",
            "value": "(?i)^.*(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?|access_?"
            "|secret_?)key(?:_?id)?|password|token|username|user_id|last.name|consumer_?(?:id|key|secret)|sign("
            "?:ed|ature)?|auth(?:entication|orization)?)",
        },
        {
            "name": "DD_IAST_REDACTION_VALUE_NUMERAL",
            "origin": "default",
            "value": "^[+-]?((0b[01]+)|(0x[0-9A-Fa-f]+)|(\\d+\\.?\\d*(?:[Ee][+-]?\\d+)?|\\.\\d+(?:[Ee][+-]?"
            "\\d+)?)|(X\\'[0-9A-Fa-f]+\\')|(B\\'[01]+\\'))$",
        },
        {
            "name": "DD_IAST_REDACTION_VALUE_PATTERN",
            "origin": "default",
            "value": "(?i)bearer\\s+[a-z0-9\\._\\-]+|token:[a-z0-9]{13}|password|gh[opsu]_[0-9a-zA-Z]{36}|ey"
            "[I-L][\\w=-]+\\.ey[I-L][\\w=-]+(\\.[\\w.+\\/=-]+)?|[\\-]{5}BEGIN[a-z\\s]+PRIVATE\\sKEY[\\-]{5}"
            "[^\\-]+[\\-]{5}END[a-z\\s]+PRIVATE\\sKEY|ssh-rsa\\s*[a-z0-9\\/\\.+]{100,}",
        },
        {"name": "DD_IAST_REQUEST_SAMPLING", "origin": "default", "value": 30.0},
        {"name": "DD_IAST_STACK_TRACE_ENABLED", "origin": "default", "value": True},
        {"name": "DD_IAST_TELEMETRY_VERBOSITY", "origin": "default", "value": "INFORMATION"},
        {"name": "DD_IAST_VULNERABILITIES_PER_REQUEST", "origin": "default", "value": 2},
        {"name": "DD_INJECTION_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_INJECT_FORCE", "origin": "env_var", "value": True},
        {"name": "DD_INSTRUMENTATION_INSTALL_ID", "origin": "default", "value": None},
        {"name": "DD_INSTRUMENTATION_INSTALL_TYPE", "origin": "default", "value": None},
        {"name": "DD_INSTRUMENTATION_TELEMETRY_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_LIVE_DEBUGGING_ENABLED", "origin": "default", "value": False},
        {"name": "DD_LLMOBS_AGENTLESS_ENABLED", "origin": "default", "value": None},
        {"name": "DD_LLMOBS_ENABLED", "origin": "default", "value": False},
        {"name": "DD_LLMOBS_ML_APP", "origin": "default", "value": None},
        {"name": "DD_LLMOBS_SAMPLE_RATE", "origin": "default", "value": 1.0},
        {"name": "DD_LOGS_INJECTION", "origin": "env_var", "value": True},
        {"name": "DD_PROFILING_AGENTLESS", "origin": "default", "value": False},
        {"name": "DD_PROFILING_API_TIMEOUT", "origin": "default", "value": 10.0},
        {"name": "DD_PROFILING_CAPTURE_PCT", "origin": "env_var", "value": 5.0},
        {"name": "DD_PROFILING_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_PROFILING_ENABLE_ASSERTS", "origin": "default", "value": False},
        {"name": "DD_PROFILING_ENABLE_CODE_PROVENANCE", "origin": "default", "value": True},
        {"name": "DD_PROFILING_ENDPOINT_COLLECTION_ENABLED", "origin": "default", "value": True},
        {"name": "DD_PROFILING_EXPORT_LIBDD_ENABLED", "origin": "default", "value": False},
        {"name": "DD_PROFILING_HEAP_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_PROFILING_HEAP_SAMPLE_SIZE", "origin": "default", "value": None},
        {"name": "DD_PROFILING_IGNORE_PROFILER", "origin": "default", "value": False},
        {"name": "DD_PROFILING_LOCK_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_PROFILING_LOCK_NAME_INSPECT_DIR", "origin": "default", "value": True},
        {"name": "DD_PROFILING_MAX_EVENTS", "origin": "default", "value": 16384},
        {"name": "DD_PROFILING_MAX_FRAMES", "origin": "env_var", "value": 512},
        {"name": "DD_PROFILING_MAX_TIME_USAGE_PCT", "origin": "default", "value": 1.0},
        {"name": "DD_PROFILING_MEMORY_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_PROFILING_MEMORY_EVENTS_BUFFER", "origin": "default", "value": 16},
        {"name": "DD_PROFILING_OUTPUT_PPROF", "origin": "default", "value": None},
        {"name": "DD_PROFILING_PYTORCH_ENABLED", "origin": "default", "value": False},
        {"name": "DD_PROFILING_PYTORCH_EVENTS_LIMIT", "origin": "default", "value": 1000000},
        {"name": "DD_PROFILING_SAMPLE_POOL_CAPACITY", "origin": "default", "value": 4},
        {"name": "DD_PROFILING_STACK_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_PROFILING_STACK_V2_ENABLED", "origin": "default", "value": True},
        {"name": "DD_PROFILING_TAGS", "origin": "default", "value": ""},
        {"name": "DD_PROFILING_TIMELINE_ENABLED", "origin": "default", "value": False},
        {"name": "DD_PROFILING_UPLOAD_INTERVAL", "origin": "env_var", "value": 10.0},
        {"name": "DD_PROFILING__FORCE_LEGACY_EXPORTER", "origin": "default", "value": False},
        {"name": "DD_REMOTE_CONFIGURATION_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS", "origin": "env_var", "value": 1.0},
        {"name": "DD_RUNTIME_METRICS_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_SERVICE", "origin": "default", "value": DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME},
        {"name": "DD_SERVICE_MAPPING", "origin": "env_var", "value": "default_dd_service:remapped_dd_service"},
        {"name": "DD_SITE", "origin": "env_var", "value": "datadoghq.com"},
        {"name": "DD_SPAN_SAMPLING_RULES", "origin": "env_var", "value": '[{"service":"xyz", "sample_rate":0.23}]'},
        {
            "name": "DD_SPAN_SAMPLING_RULES_FILE",
            "origin": "env_var",
            "value": str(file),
        },
        {"name": "DD_SYMBOL_DATABASE_INCLUDES", "origin": "default", "value": "set()"},
        {"name": "DD_SYMBOL_DATABASE_UPLOAD_ENABLED", "origin": "default", "value": True},
        {"name": "DD_TAGS", "origin": "env_var", "value": "team:apm,component:web"},
        {"name": "DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED", "origin": "default", "value": True},
        {"name": "DD_TELEMETRY_HEARTBEAT_INTERVAL", "origin": "default", "value": 60},
        {"name": "DD_TESTING_RAISE", "origin": "env_var", "value": True},
        {"name": "DD_TEST_SESSION_NAME", "origin": "default", "value": None},
        {"name": "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED", "origin": "default", "value": False},
        {"name": "DD_TRACE_AGENT_HOSTNAME", "origin": "default", "value": None},
        {"name": "DD_TRACE_AGENT_PORT", "origin": "default", "value": None},
        {"name": "DD_TRACE_AGENT_TIMEOUT_SECONDS", "origin": "default", "value": 2.0},
        {"name": "DD_TRACE_API_VERSION", "origin": "env_var", "value": "v0.5"},
        {"name": "DD_TRACE_CLIENT_IP_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_CLIENT_IP_HEADER", "origin": "default", "value": None},
        {"name": "DD_TRACE_COMPUTE_STATS", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_DEBUG", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED", "origin": "default", "value": "set()"},
        {"name": "DD_TRACE_EXPERIMENTAL_RUNTIME_ID_ENABLED", "origin": "default", "value": False},
        {"name": "DD_TRACE_HEADER_TAGS", "origin": "default", "value": ""},
        {"name": "DD_TRACE_HEALTH_METRICS_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_HTTP_CLIENT_TAG_QUERY_STRING", "origin": "default", "value": "true"},
        {"name": "DD_TRACE_HTTP_SERVER_ERROR_STATUSES", "origin": "default", "value": "500-599"},
        {"name": "DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED", "origin": "default", "value": False},
        {"name": "DD_TRACE_LOG_FILE", "origin": "default", "value": None},
        {"name": "DD_TRACE_LOG_FILE_LEVEL", "origin": "default", "value": "DEBUG"},
        {"name": "DD_TRACE_LOG_FILE_SIZE_BYTES", "origin": "default", "value": 15728640},
        {"name": "DD_TRACE_LOG_STREAM_HANDLER", "origin": "default", "value": True},
        {"name": "DD_TRACE_METHODS", "origin": "default", "value": None},
        {"name": "DD_TRACE_NATIVE_SPAN_EVENTS", "origin": "default", "value": False},
        {"name": "DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP", "origin": "env_var", "value": ".*"},
        {"name": "DD_TRACE_OTEL_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_PARTIAL_FLUSH_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_TRACE_PARTIAL_FLUSH_MIN_SPANS", "origin": "env_var", "value": 3},
        {
            "name": "DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED",
            "origin": "default",
            "value": False,
        },
        {
            "name": "DD_TRACE_PEER_SERVICE_MAPPING",
            "origin": "env_var",
            "value": "default_service:remapped_service",
        },
        {"name": "DD_TRACE_PROPAGATION_BEHAVIOR_EXTRACT", "origin": "env_var", "value": "restart"},
        {"name": "DD_TRACE_PROPAGATION_EXTRACT_FIRST", "origin": "default", "value": False},
        {"name": "DD_TRACE_PROPAGATION_HTTP_BAGGAGE_ENABLED", "origin": "default", "value": False},
        {"name": "DD_TRACE_PROPAGATION_STYLE_EXTRACT", "origin": "env_var", "value": "tracecontext"},
        {"name": "DD_TRACE_PROPAGATION_STYLE_INJECT", "origin": "env_var", "value": "tracecontext"},
        {"name": "DD_TRACE_RATE_LIMIT", "origin": "env_var", "value": 50},
        {"name": "DD_TRACE_REPORT_HOSTNAME", "origin": "default", "value": False},
        {
            "name": "DD_TRACE_SAMPLING_RULES",
            "origin": "env_var",
            "value": '[{"sample_rate":1.0,"service":"xyz","name":"abc"}]',
        },
        {"name": "DD_TRACE_SPAN_TRACEBACK_MAX_SIZE", "origin": "default", "value": 30},
        {"name": "DD_TRACE_STARTUP_LOGS", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_WRITER_BUFFER_SIZE_BYTES", "origin": "env_var", "value": 1000},
        {"name": "DD_TRACE_WRITER_INTERVAL_SECONDS", "origin": "env_var", "value": 30.0},
        {"name": "DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES", "origin": "env_var", "value": 9999},
        {"name": "DD_TRACE_WRITER_REUSE_CONNECTIONS", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH", "origin": "default", "value": 512},
        {"name": "DD_USER_MODEL_EMAIL_FIELD", "origin": "default", "value": ""},
        {"name": "DD_USER_MODEL_LOGIN_FIELD", "origin": "default", "value": ""},
        {"name": "DD_USER_MODEL_NAME_FIELD", "origin": "default", "value": ""},
        {"name": "DD_VERSION", "origin": "default", "value": None},
        {"name": "_DD_APPSEC_DEDUPLICATION_ENABLED", "origin": "default", "value": True},
        {"name": "_DD_IAST_LAZY_TAINT", "origin": "default", "value": False},
        {"name": "_DD_TRACE_WRITER_LOG_ERROR_PAYLOADS", "origin": "default", "value": False},
        {"name": "instrumentation_source", "origin": "code", "value": "ddtrace.auto"},
        {"name": "python_build_gnu_type", "origin": "unknown", "value": sysconfig.get_config_var("BUILD_GNU_TYPE")},
        {"name": "python_host_gnu_type", "origin": "unknown", "value": sysconfig.get_config_var("HOST_GNU_TYPE")},
        {"name": "python_soabi", "origin": "unknown", "value": sysconfig.get_config_var("SOABI")},
    ]
    assert configurations == expected, configurations


def test_update_dependencies_event(test_agent_session, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    # app-started events are sent 10 seconds after ddtrace imported, this configuration overrides this
    # behavior to force the app-started event to be queued immediately
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    # Import httppretty after ddtrace is imported, this ensures that the module is sent in a dependencies event
    # Imports httpretty twice and ensures only one dependency entry is sent
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess("import xmltodict", env=env)
    assert status == 0, stderr
    deps = test_agent_session.get_dependencies("xmltodict")
    assert len(deps) == 1, deps


def test_instrumentation_source_config(
    test_agent_session, ddtrace_run_python_code_in_subprocess, run_python_code_in_subprocess
):
    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = call_program("ddtrace-run", sys.executable, "-c", "", env=env)
    assert status == 0, stderr
    configs = test_agent_session.get_configurations("instrumentation_source")
    assert configs and configs[-1]["value"] == "cmd_line"

    _, stderr, status, _ = call_program(sys.executable, "-c", "import ddtrace.auto", env=env)
    assert status == 0, stderr
    configs = test_agent_session.get_configurations("instrumentation_source")
    assert configs and configs[-1]["value"] == "ddtrace.auto"


def test_update_dependencies_event_when_disabled(test_agent_session, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    # app-started events are sent 10 seconds after ddtrace imported, this configuration overrides this
    # behavior to force the app-started event to be queued immediately
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"
    env["DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED"] = "false"

    # Import httppretty after ddtrace is imported, this ensures that the module is sent in a dependencies event
    # Imports httpretty twice and ensures only one dependency entry is sent
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess("import xmltodict", env=env)
    events = test_agent_session.get_events("app-dependencies-loaded", subprocess=True)
    assert len(events) == 0, events


def test_update_dependencies_event_not_stdlib(test_agent_session, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    # app-started events are sent 10 seconds after ddtrace imported, this configuration overrides this
    # behavior to force the app-started event to be queued immediately
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    # Import httppretty after ddtrace is imported, this ensures that the module is sent in a dependencies event
    # Imports httpretty twice and ensures only one dependency entry is sent
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import sys
import httpretty
del sys.modules["httpretty"]
import httpretty
""",
        env=env,
    )
    assert status == 0, stderr
    deps = test_agent_session.get_dependencies("httpretty")
    assert len(deps) == 1, deps


def test_app_closing_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that app_shutdown() queues and sends an app-closing telemetry request"""
    # app started event must be queued before any other telemetry event
    telemetry_writer._app_started(register_app_shutdown=False)
    assert telemetry_writer.started
    # send app closed event
    telemetry_writer.app_shutdown()

    requests = test_agent_session.get_requests("app-closing")
    assert len(requests) == 1
    # ensure a valid request body was sent
    totel_events = len(test_agent_session.get_events())
    assert requests[0]["body"] == _get_request_body({}, "app-closing", totel_events)


def test_add_integration(telemetry_writer, test_agent_session, mock_time):
    """asserts that add_integration() queues a valid telemetry request"""
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        # queue integrations
        telemetry_writer.add_integration("integration-t", True, True, "")
        telemetry_writer.add_integration("integration-f", False, False, "terrible failure")
        # send integrations to the agent
        telemetry_writer.periodic(force_flush=True)

        requests = test_agent_session.get_requests("app-integrations-change")
        # assert integration change telemetry request was sent
        assert len(requests) == 1

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
        assert requests[0]["body"] == _get_request_body(expected_payload, "app-integrations-change", seq_id=2)


def test_app_client_configuration_changed_event(telemetry_writer, test_agent_session, mock_time):
    # force periodic call to flush the first app_started call
    telemetry_writer.periodic(force_flush=True)
    """asserts that queuing a configuration sends a valid telemetry request"""
    with override_global_config(dict()):
        telemetry_writer.add_configuration("appsec_enabled", True)
        telemetry_writer.add_configuration("DD_TRACE_PROPAGATION_STYLE_EXTRACT", "datadog")
        telemetry_writer.add_configuration("appsec_enabled", False, "env_var")

        telemetry_writer.periodic(force_flush=True)

        events = test_agent_session.get_events("app-client-configuration-change")
        received_configurations = [c for event in events for c in event["payload"]["configuration"]]
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
    telemetry_writer.periodic(force_flush=True)
    assert len(test_agent_session.get_requests("app-integrations-change")) == 0


@pytest.mark.parametrize("mock_status", [300, 400, 401, 403, 500])
def test_send_failing_request(mock_status, telemetry_writer):
    """asserts that a warning is logged when an unsuccessful response is returned by the http client"""

    with override_global_config(dict(_telemetry_dependency_collection=False)):
        # force periodic call to flush the first app_started call
        telemetry_writer.periodic(force_flush=True)
        with httpretty.enabled():
            httpretty.register_uri(httpretty.POST, telemetry_writer._client.url, status=mock_status)
            with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
                # sends failing app-heartbeat event
                telemetry_writer.periodic(force_flush=True)
                # asserts unsuccessful status code was logged
                log.debug.assert_called_with(
                    "Failed to send Instrumentation Telemetry to %s. response: %s",
                    telemetry_writer._client.url,
                    mock_status,
                )


def test_app_heartbeat_event_periodic(mock_time, telemetry_writer, test_agent_session):
    # type: (mock.Mock, Any, Any) -> None
    """asserts that we queue/send app-heartbeat when periodc() is called"""
    # Ensure telemetry writer is initialized to send periodic events
    telemetry_writer._is_periodic = True
    telemetry_writer.started = True
    # Assert default telemetry interval is 10 seconds and the expected periodic threshold and counts are set
    assert telemetry_writer.interval == 10
    assert telemetry_writer._periodic_threshold == 5
    assert telemetry_writer._periodic_count == 0

    # Assert next flush contains app-heartbeat event
    for _ in range(telemetry_writer._periodic_threshold):
        telemetry_writer.periodic()
        assert test_agent_session.get_events("app-heartbeat", filter_heartbeats=False) == []

    telemetry_writer.periodic()
    heartbeat_events = test_agent_session.get_events("app-heartbeat", filter_heartbeats=False)
    assert len(heartbeat_events) == 1


def test_app_heartbeat_event(mock_time, telemetry_writer, test_agent_session):
    # type: (mock.Mock, Any, Any) -> None
    """asserts that we queue/send app-heartbeat event every 60 seconds when app_heartbeat_event() is called"""
    # Assert a maximum of one heartbeat is queued per flush
    telemetry_writer.periodic(force_flush=True)
    events = test_agent_session.get_events("app-heartbeat", filter_heartbeats=False)
    assert len(events) > 0


def test_app_product_change_event(mock_time, telemetry_writer, test_agent_session):
    # type: (mock.Mock, Any, Any) -> None
    """asserts that enabling or disabling an APM Product triggers a valid telemetry request"""

    # Assert that the default product status is disabled
    assert any(telemetry_writer._product_enablement.values()) is False

    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.LLMOBS, True)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION, True)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, True)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, True)
    assert all(telemetry_writer._product_enablement.values())

    telemetry_writer._app_started()

    # Assert that there's only an app_started event (since product activation happened before the app started)
    events = test_agent_session.get_events("app-product-change")
    telemetry_writer.periodic(force_flush=True)
    assert not len(events)

    # Assert that unchanged status doesn't generate the event
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, True)
    telemetry_writer.periodic(force_flush=True)
    events = test_agent_session.get_events("app-product-change")
    assert not len(events)

    # Assert that a single event is generated
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, False)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION, False)
    telemetry_writer.periodic(force_flush=True)
    events = test_agent_session.get_events("app-product-change")
    assert len(events) == 1

    # Assert that payload is as expected
    assert events[0]["request_type"] == "app-product-change"
    products = events[0]["payload"]["products"]
    version = _pep440_to_semver()
    assert products == {
        TELEMETRY_APM_PRODUCT.APPSEC.value: {"enabled": False, "version": version},
        TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION.value: {"enabled": False, "version": version},
        TELEMETRY_APM_PRODUCT.LLMOBS.value: {"enabled": True, "version": version},
        TELEMETRY_APM_PRODUCT.PROFILER.value: {"enabled": True, "version": version},
    }


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


def test_telemetry_writer_agent_setup():
    with override_global_config(
        {"_dd_site": "datad0g.com", "_dd_api_key": "foobarkey", "_ci_visibility_agentless_enabled": False}
    ):
        new_telemetry_writer = ddtrace.internal.telemetry.TelemetryWriter(agentless=False)
        assert new_telemetry_writer._enabled
        assert new_telemetry_writer._client._endpoint == "telemetry/proxy/api/v2/apmtelemetry"
        assert "http://" in new_telemetry_writer._client._telemetry_url
        assert ":9126" in new_telemetry_writer._client._telemetry_url
        assert "dd-api-key" not in new_telemetry_writer._client._headers


@pytest.mark.parametrize(
    "env_agentless,arg_agentless,expected_endpoint",
    [
        (True, True, "api/v2/apmtelemetry"),
        (True, False, "telemetry/proxy/api/v2/apmtelemetry"),
        (False, True, "api/v2/apmtelemetry"),
        (False, False, "telemetry/proxy/api/v2/apmtelemetry"),
    ],
)
def test_telemetry_writer_agent_setup_agentless_arg_overrides_env(env_agentless, arg_agentless, expected_endpoint):
    with override_global_config(
        {"_dd_site": "datad0g.com", "_dd_api_key": "foobarkey", "_ci_visibility_agentless_enabled": env_agentless}
    ):
        new_telemetry_writer = ddtrace.internal.telemetry.TelemetryWriter(agentless=arg_agentless)
        # Note: other tests are checking whether values bet set properly, so we're only looking at agentlessness here
        assert new_telemetry_writer._client._endpoint == expected_endpoint


@pytest.mark.subprocess(
    env={"DD_SITE": "datad0g.com", "DD_API_KEY": "foobarkey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
)
def test_telemetry_writer_agentless_setup():
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._enabled
    assert telemetry_writer._client._endpoint == "api/v2/apmtelemetry"
    assert telemetry_writer._client._telemetry_url == "https://all-http-intake.logs.datad0g.com"
    assert telemetry_writer._client._headers["dd-api-key"] == "foobarkey"


@pytest.mark.subprocess(
    env={"DD_SITE": "datadoghq.eu", "DD_API_KEY": "foobarkey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
)
def test_telemetry_writer_agentless_setup_eu():
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._enabled
    assert telemetry_writer._client._endpoint == "api/v2/apmtelemetry"
    assert telemetry_writer._client._telemetry_url == "https://instrumentation-telemetry-intake.datadoghq.eu"
    assert telemetry_writer._client._headers["dd-api-key"] == "foobarkey"


@pytest.mark.subprocess(env={"DD_SITE": "datad0g.com", "DD_API_KEY": "", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"})
def test_telemetry_writer_agentless_disabled_without_api_key():
    from ddtrace.internal.telemetry import telemetry_writer

    assert not telemetry_writer._enabled
    assert telemetry_writer._client._endpoint == "api/v2/apmtelemetry"
    assert telemetry_writer._client._telemetry_url == "https://all-http-intake.logs.datad0g.com"
    assert "dd-api-key" not in telemetry_writer._client._headers


@pytest.mark.subprocess(env={"DD_SITE": "datad0g.com", "DD_API_KEY": "foobarkey"})
def test_telemetry_writer_is_using_agentless_by_default_if_api_key_is_available():
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._enabled
    assert telemetry_writer._client._endpoint == "api/v2/apmtelemetry"
    assert telemetry_writer._client._telemetry_url == "https://all-http-intake.logs.datad0g.com"
    assert telemetry_writer._client._headers["dd-api-key"] == "foobarkey"


@pytest.mark.subprocess(env={"DD_API_KEY": "", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "false"})
def test_telemetry_writer_is_using_agent_by_default_if_api_key_is_not_available():
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._enabled
    assert telemetry_writer._client._endpoint == "telemetry/proxy/api/v2/apmtelemetry"
    assert telemetry_writer._client._telemetry_url in ("http://localhost:9126", "http://testagent:9126")
    assert "dd-api-key" not in telemetry_writer._client._headers


def test_otel_config_telemetry(test_agent_session, run_python_code_in_subprocess, tmpdir):
    """
    asserts that telemetry data is submitted for OpenTelemetry configurations
    """

    env = os.environ.copy()
    env["DD_SERVICE"] = "dd_service"
    env["OTEL_SERVICE_NAME"] = "otel_service"
    env["OTEL_LOG_LEVEL"] = "DEBUG"
    env["OTEL_PROPAGATORS"] = "tracecontext"
    env["OTEL_TRACES_SAMPLER"] = "always_on"
    env["OTEL_TRACES_EXPORTER"] = "none"
    env["OTEL_LOGS_EXPORTER"] = "otlp"
    env["OTEL_RESOURCE_ATTRIBUTES"] = "team=apm,component=web"
    env["OTEL_SDK_DISABLED"] = "true"
    env["OTEL_UNSUPPORTED_CONFIG"] = "value"
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace", env=env)
    assert status == 0, stderr

    configurations = {c["name"]: c for c in test_agent_session.get_configurations()}

    assert configurations["DD_SERVICE"] == {"name": "DD_SERVICE", "origin": "env_var", "value": "dd_service"}
    assert configurations["OTEL_LOG_LEVEL"] == {"name": "OTEL_LOG_LEVEL", "origin": "env_var", "value": "debug"}
    assert configurations["OTEL_PROPAGATORS"] == {
        "name": "OTEL_PROPAGATORS",
        "origin": "env_var",
        "value": "tracecontext",
    }
    assert configurations["OTEL_TRACES_SAMPLER"] == {
        "name": "OTEL_TRACES_SAMPLER",
        "origin": "env_var",
        "value": "always_on",
    }
    assert configurations["OTEL_TRACES_EXPORTER"] == {
        "name": "OTEL_TRACES_EXPORTER",
        "origin": "env_var",
        "value": "none",
    }
    assert configurations["OTEL_LOGS_EXPORTER"] == {"name": "OTEL_LOGS_EXPORTER", "origin": "env_var", "value": "otlp"}
    assert configurations["OTEL_RESOURCE_ATTRIBUTES"] == {
        "name": "OTEL_RESOURCE_ATTRIBUTES",
        "origin": "env_var",
        "value": "team=apm,component=web",
    }
    assert configurations["OTEL_SDK_DISABLED"] == {"name": "OTEL_SDK_DISABLED", "origin": "env_var", "value": "true"}

    env_hiding_metrics = test_agent_session.get_metrics("otel.env.hiding")
    tags = [m["tags"] for m in env_hiding_metrics]
    assert tags == [["config_opentelemetry:otel_service_name", "config_datadog:dd_service"]]

    env_unsupported_metrics = test_agent_session.get_metrics("otel.env.unsupported")
    tags = [m["tags"] for m in env_unsupported_metrics]
    assert tags == [["config_opentelemetry:otel_unsupported_config"]]

    env_invalid_metrics = test_agent_session.get_metrics("otel.env.invalid")
    tags = [m["tags"] for m in env_invalid_metrics]
    assert tags == [["config_opentelemetry:otel_logs_exporter"]]
