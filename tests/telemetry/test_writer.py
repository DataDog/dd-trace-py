import os
import sys
import sysconfig
from typing import Any
from typing import Optional
from unittest import mock

import pytest

from ddtrace import config
from ddtrace.internal.settings._agent import get_agent_hostname
import ddtrace.internal.settings._core as settings_core
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.settings._telemetry import config as telemetry_config
import ddtrace.internal.telemetry
from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.internal.telemetry.data import get_application
from ddtrace.internal.telemetry.data import get_host_info
from ddtrace.internal.telemetry.writer import TelemetryWriter
from ddtrace.internal.telemetry.writer import get_runtime_id
from ddtrace.internal.utils.version import _pep440_to_semver
from tests.conftest import DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME
from tests.utils import call_program
from tests.utils import override_global_config


class _SyntheticDDConfig(DDConfig):
    """
    A minimal DDConfig used to exercise report_configuration()'s own walking logic
    (public/private/sensitive filtering, source + config_id resolution) without any
    knowledge of real product settings.
    """

    __prefix__ = "dd.test.synthetic"

    public_setting = DDConfig.v(str, "public_setting", default="pub_default")
    _private_setting = DDConfig.v(str, "private_setting", default="priv_default", private=True)
    sensitive_setting = DDConfig.v(str, "sensitive_setting", default="sens_default")
    bool_setting = DDConfig.v(bool, "bool_setting", default=False)
    float_setting = DDConfig.v(float, "float_setting", default=0.0)
    # NOTE: DDConfig.config_id is a single instance attribute overwritten during __init__'s
    # field-iteration loop, not a per-field map, so it only reflects whichever fleet-sourced
    # field was processed last. Keep this field last so the config_id assertion below is stable.
    fleet_setting = DDConfig.v(str, "fleet_setting", default="fleet_default")


@pytest.fixture(autouse=True)
def _no_inherited_api_key(monkeypatch):
    """Keep subprocess telemetry writers in non-agentless mode.

    A ``DD_API_KEY`` present in the test environment is inherited by the subprocesses these tests
    spawn (``os.environ.copy()``) and flips their telemetry writer into agentless mode, diverting
    requests to the Datadog intake instead of the local test agent. Tests that genuinely need an
    api key set it explicitly via the subprocess marker env / mock.patch.dict, which overrides
    this removal.
    """
    monkeypatch.delenv("DD_API_KEY", raising=False)


def _to_config_str(value):
    """Mirror the native worker's configuration value serialization.

    The native TelemetryWorker serializes each configuration ``value`` (see
    ``TelemetryWriter.add_configuration`` / ``_config_value_to_str``: ``None`` stays ``None``
    (a JSON ``null``), booleans become lowercase ``"true"``/``"false"``, everything else
    ``str(value)`` after dict/list flattening). So tests that assert typed values
    (bool/int/float/None/list) must compare against this wire form.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return ",".join(":".join((k, str(v))) for k, v in value.items())
    if isinstance(value, (set, frozenset)):
        return ",".join(sorted(str(v) for v in value))
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


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
    # Keep the subprocess writer non-agentless (a stray DD_API_KEY would route to intake).
    env.pop("DD_API_KEY", None)
    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace.auto", env=env)
    assert status == 0, stderr

    configuration = test_agent_session.get_configurations(name=env_var, remove_seq_id=True, effective=True)
    assert len(configuration) == 1, configuration
    assert configuration[0] == {"name": env_var, "origin": "env_var", "value": _to_config_str(expected_value)}


def test_app_started_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that app-started is emitted exactly once with a valid body"""
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        # The native worker emits app-started eagerly on start() as its own request, so we assert
        # on the app-started event content (it appears exactly once) rather than the total request
        # count, which now also includes the eager app-started + heartbeat/closing lifecycle events.
        telemetry_writer.periodic(force_flush=True)
        app_started_events = test_agent_session.get_events("app-started")
        assert len(app_started_events) == 1
        validate_request_body(app_started_events[0], None, "app-started")
        # app-started carries at least a configuration list (products may be null when nothing is
        # activated before start, since the native worker reports activations as app-product-change).
        assert app_started_events[0]["payload"].get("configuration")

        # app-started always reports the interpreter's build info, sourced from sysconfig
        # rather than from any product configuration, so it's telemetry's own data, not a
        # dependency on another component's settings.
        configs_by_name = {c["name"]: c for c in app_started_events[0]["payload"]["configuration"]}
        for name, sysconfig_key in (
            ("python_soabi", "SOABI"),
            ("python_host_gnu_type", "HOST_GNU_TYPE"),
            ("python_build_gnu_type", "BUILD_GNU_TYPE"),
        ):
            assert configs_by_name[name]["origin"] == "unknown"
            assert configs_by_name[name]["value"] == sysconfig.get_config_var(sysconfig_key)


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
import ddtrace.internal.settings.symbol_db
import ddtrace.internal.settings.dynamic_instrumentation
import ddtrace.internal.settings.exception_replay
import opentelemetry
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
    env["DD_LOGS_OTEL_ENABLED"] = "True"
    env["DD_METRICS_OTEL_ENABLED"] = "True"
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"

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
    env["DD_INJECTION_ENABLED"] = "tracer"
    # These two are normally set globally by the riot test harness (riotfile.py), which is the
    # canonical way this suite runs; the expected list below asserts their ``env_var`` origin. Set
    # them explicitly so the subprocess sees the same values regardless of how the outer test runner
    # is invoked (bare pytest vs. riot).
    env["DD_TESTING_RAISE"] = "1"
    env["DD_CODE_ORIGIN_FOR_SPANS_ENABLED"] = "false"

    # Ensures app-started event is queued immediately after ddtrace is imported
    # instead of waiting for 10 seconds.
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"
    # Keep the subprocess writer non-agentless (a stray DD_API_KEY would route to intake).
    env.pop("DD_API_KEY", None)

    _, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    # DD_TRACE_AGENT_URL in gitlab is different from CI, to keep things simple we will
    # skip validating this config
    configurations = test_agent_session.get_configurations(
        ignores=["DD_TRACE_AGENT_URL", "DD_AGENT_PORT", "DD_TRACE_AGENT_PORT"], remove_seq_id=True, effective=True
    )
    assert configurations
    configurations.sort(key=lambda x: x["name"])

    expected = [
        {"name": "DD_AGENT_HOST", "origin": "default", "value": None},
        {"name": "DD_API_SECURITY_DOWNSTREAM_BODY_ANALYSIS_SAMPLE_RATE", "origin": "default", "value": 0.5},
        {"name": "DD_API_SECURITY_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_API_SECURITY_ENDPOINT_COLLECTION_ENABLED", "origin": "default", "value": True},
        {"name": "DD_API_SECURITY_ENDPOINT_COLLECTION_MESSAGE_LIMIT", "origin": "default", "value": 300},
        {"name": "DD_API_SECURITY_MAX_DOWNSTREAM_REQUEST_BODY_ANALYSIS", "origin": "default", "value": 1},
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
            "value": r"(?i)(?:p(?:ass)?w(?:or)?d|pass(?:[_-]?phrase)?|"
            r"secret(?:[_-]?key)?|(?:(?:api|private|public|access)[_-]?)"
            r"key(?:[_-]?id)?|(?:(?:auth|access|id|refresh)[_-]?)?token|"
            r"consumer[_-]?(?:id|key|secret)|sign(?:ed|ature)?"
            r"|auth(?:entication|orization)?|jsessionid|phpsessid|asp\.net(?:[_-]|-)sessionid|sid|jwt)"
            r'(?:\s*=([^;&]+)|"\s*:\s*("[^"]+"|\d+))|bearer\s+([a-z0-9\._\-]+)|'
            r"token\s*:\s*([a-z0-9]{13})|gh[opsu]_([0-9a-zA-Z]{36})"
            r"|ey[I-L][\w=-]+\.(ey[I-L][\w=-]+(?:\.[\w.+\/=-]+)?)|[\-]{5}BEGIN[a-z\s]+PRIVATE\sKEY[\-]{5}([^\-]+)[\-]"
            r"{5}END[a-z\s]+PRIVATE\sKEY|"
            r"ssh-rsa\s*([a-z0-9\/\.+]{100,})",
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
        {"name": "DD_CODE_ORIGIN_FOR_SPANS_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_CRASHTRACKING_COLLECT_ALL_THREADS", "origin": "default", "value": True},
        {"name": "DD_CRASHTRACKING_CREATE_ALT_STACK", "origin": "default", "value": True},
        {"name": "DD_CRASHTRACKING_DEBUG_URL", "origin": "default", "value": None},
        {"name": "DD_CRASHTRACKING_ENABLED", "origin": "default", "value": True},
        {"name": "DD_CRASHTRACKING_ERRORS_INTAKE_ENABLED", "origin": "default", "value": True},
        {"name": "DD_CRASHTRACKING_MAX_THREADS", "origin": "default", "value": 128},
        {
            "name": "DD_CRASHTRACKING_STACKTRACE_RESOLVER",
            "origin": "default",
            "value": "safe" if sys.platform == "linux" else "full",
        },
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
        {"name": "DD_DYNAMIC_INSTRUMENTATION_PROBE_FILE", "origin": "default", "value": None},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_REDACTED_IDENTIFIERS", "origin": "default", "value": ""},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_REDACTED_TYPES", "origin": "default", "value": ""},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_REDACTION_EXCLUDED_IDENTIFIERS", "origin": "default", "value": ""},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_UPLOAD_INTERVAL_SECONDS", "origin": "default", "value": 1.0},
        {"name": "DD_DYNAMIC_INSTRUMENTATION_UPLOAD_TIMEOUT", "origin": "default", "value": 30},
        {"name": "DD_ENV", "origin": "default", "value": None},
        {"name": "DD_ERROR_TRACKING_HANDLED_ERRORS", "origin": "default", "value": ""},
        {"name": "DD_ERROR_TRACKING_HANDLED_ERRORS_INCLUDE", "origin": "default", "value": ""},
        {"name": "DD_EXCEPTION_REPLAY_CAPTURE_MAX_FRAMES", "origin": "default", "value": 8},
        {"name": "DD_EXCEPTION_REPLAY_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED", "origin": "default", "value": True},
        {"name": "DD_FASTAPI_ASYNC_BODY_TIMEOUT_SECONDS", "origin": "default", "value": 0.1},
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
        {"name": "DD_IAST_SECURITY_CONTROLS_CONFIGURATION", "origin": "default", "value": ""},
        {"name": "DD_IAST_STACK_TRACE_ENABLED", "origin": "default", "value": True},
        {"name": "DD_IAST_TELEMETRY_VERBOSITY", "origin": "default", "value": "INFORMATION"},
        {"name": "DD_IAST_TRUNCATION_MAX_VALUE_LENGTH", "origin": "default", "value": 250},
        {"name": "DD_IAST_VULNERABILITIES_PER_REQUEST", "origin": "default", "value": 2},
        {"name": "DD_INJECTION_ENABLED", "origin": "env_var", "value": "tracer"},
        {"name": "DD_INJECT_FORCE", "origin": "env_var", "value": True},
        {"name": "DD_INSTRUMENTATION_TELEMETRY_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_LIVE_DEBUGGING_ENABLED", "origin": "default", "value": False},
        {"name": "DD_LLMOBS_AGENTLESS_ENABLED", "origin": "default", "value": None},
        {"name": "DD_LLMOBS_ENABLED", "origin": "default", "value": False},
        {"name": "DD_LLMOBS_EVALUATOR_SAMPLING_RULES", "origin": "env_var", "value": None},
        {"name": "DD_LLMOBS_EVENT_SIZE_BYTES", "origin": "default", "value": 5000000},
        {"name": "DD_LLMOBS_INSTRUMENTED_PROXY_URLS", "origin": "default", "value": None},
        {"name": "DD_LLMOBS_ML_APP", "origin": "default", "value": None},
        {"name": "DD_LLMOBS_PAYLOAD_SIZE_BYTES", "origin": "default", "value": 5242880},
        {"name": "DD_LLMOBS_SAMPLE_RATE", "origin": "default", "value": 1.0},
        {"name": "DD_LOGS_INJECTION", "origin": "env_var", "value": True},
        {"name": "DD_LOGS_OTEL_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_METRICS_OTEL_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_MODEL_LAB_ENABLED", "origin": "default", "value": False},
        {"name": "DD_PROFILING_AGENTLESS", "origin": "default", "value": False},
        {"name": "DD_PROFILING_API_TIMEOUT_MS", "origin": "default", "value": 10000},
        {"name": "DD_PROFILING_CAPTURE_PCT", "origin": "env_var", "value": 5.0},
        {"name": "DD_PROFILING_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_PROFILING_ENABLE_ASSERTS", "origin": "default", "value": False},
        {"name": "DD_PROFILING_ENABLE_CODE_PROVENANCE", "origin": "default", "value": True},
        {"name": "DD_PROFILING_ENDPOINT_COLLECTION_ENABLED", "origin": "default", "value": True},
        {"name": "DD_PROFILING_EXCEPTION_COLLECT_MESSAGE", "origin": "default", "value": False},
        {"name": "DD_PROFILING_EXCEPTION_ENABLED", "origin": "default", "value": False},
        {"name": "DD_PROFILING_EXCEPTION_SAMPLING_INTERVAL", "origin": "default", "value": 100},
        {"name": "DD_PROFILING_HEAP_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_PROFILING_HEAP_SAMPLE_SIZE", "origin": "default", "value": None},
        {"name": "DD_PROFILING_IGNORE_PROFILER", "origin": "default", "value": False},
        {"name": "DD_PROFILING_LOCK_ENABLED", "origin": "env_var", "value": False},
        {
            "name": "DD_PROFILING_LOCK_EXCLUDE_MODULES",
            "origin": "default",
            "value": ",".join(
                sorted(
                    [
                        "anyio",
                        "asyncio",
                        "bytecode",
                        "concurrent",
                        "datadog",
                        "ddsketch",
                        "ddtrace",
                        "envier",
                        "gunicorn",
                        "h11",
                        "http",
                        "logging",
                        "threading",
                        "uvicorn",
                        "werkzeug",
                        "wrapt",
                    ]
                )
            ),
        },
        {"name": "DD_PROFILING_LOCK_NAME_INSPECT_DIR", "origin": "default", "value": True},
        {"name": "DD_PROFILING_MAX_FRAMES", "origin": "env_var", "value": 512},
        {"name": "DD_PROFILING_MAX_TIME_USAGE_PCT", "origin": "default", "value": 1.0},
        {"name": "DD_PROFILING_MEMORY_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_PROFILING_MEMORY_EVENTS_BUFFER", "origin": "default", "value": 16},
        {"name": "DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED", "origin": "default", "value": False},
        {"name": "DD_PROFILING_OUTPUT_PPROF", "origin": "default", "value": None},
        {"name": "DD_PROFILING_PYTORCH_ENABLED", "origin": "default", "value": False},
        {"name": "DD_PROFILING_PYTORCH_EVENTS_LIMIT", "origin": "default", "value": 1000000},
        {"name": "DD_PROFILING_PYTORCH_MAX_FRAMES", "origin": "default", "value": 128},
        {"name": "DD_PROFILING_SAMPLE_POOL_CAPACITY", "origin": "default", "value": 4},
        {"name": "DD_PROFILING_STACK_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_PROFILING_STACK_NATIVE_FRAMES", "origin": "default", "value": True},
        {"name": "DD_PROFILING_STACK_UVLOOP", "origin": "default", "value": True},
        {"name": "DD_PROFILING_TAGS", "origin": "default", "value": ""},
        {"name": "DD_PROFILING_TIMELINE_ENABLED", "origin": "default", "value": True},
        {"name": "DD_PROFILING_UPLOAD_INTERVAL", "origin": "env_var", "value": 10.0},
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
        {"name": "DD_SYMBOL_DATABASE_INCLUDES", "origin": "default", "value": ""},
        {"name": "DD_SYMBOL_DATABASE_UPLOAD_ENABLED", "origin": "default", "value": True},
        {"name": "DD_TAGS", "origin": "env_var", "value": "team:apm,component:web"},
        {"name": "DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED", "origin": "default", "value": True},
        {"name": "DD_TELEMETRY_HEARTBEAT_INTERVAL", "origin": "default", "value": 60},
        {"name": "DD_TESTING_RAISE", "origin": "env_var", "value": True},
        {"name": "DD_TEST_SESSION_NAME", "origin": "default", "value": None},
        {"name": "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED", "origin": "default", "value": False},
        {"name": "DD_TRACE_AGENT_HOSTNAME", "origin": "default", "value": None},
        {"name": "DD_TRACE_AGENT_TIMEOUT_SECONDS", "origin": "default", "value": 2.0},
        {"name": "DD_TRACE_API_VERSION", "origin": "env_var", "value": "v0.5"},
        {"name": "DD_TRACE_BAGGAGE_TAG_KEYS", "origin": "default", "value": "user.id,account.id,session.id"},
        {"name": "DD_TRACE_CLIENT_IP_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_CLIENT_IP_HEADER", "origin": "default", "value": None},
        {"name": "DD_TRACE_DEBUG", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_ENABLED", "origin": "env_var", "value": False},
        {"name": "DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED", "origin": "default", "value": ""},
        {"name": "DD_TRACE_EXPERIMENTAL_LONG_RUNNING_FLUSH_INTERVAL", "origin": "default", "value": 120.0},
        {"name": "DD_TRACE_EXPERIMENTAL_LONG_RUNNING_INITIAL_FLUSH_INTERVAL", "origin": "default", "value": 10.0},
        {"name": "DD_TRACE_EXPERIMENTAL_RUNTIME_ID_ENABLED", "origin": "default", "value": False},
        {"name": "DD_TRACE_HEADER_TAGS", "origin": "default", "value": ""},
        {"name": "DD_TRACE_HEALTH_METRICS_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_HTTP_CLIENT_TAG_QUERY_STRING", "origin": "default", "value": "true"},
        {"name": "DD_TRACE_HTTP_SERVER_ERROR_STATUSES", "origin": "default", "value": "500-599"},
        {"name": "DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED", "origin": "default", "value": False},
        {"name": "DD_TRACE_LOG_FILE", "origin": "default", "value": None},
        {"name": "DD_TRACE_LOG_FILE_LEVEL", "origin": "default", "value": "DEBUG"},
        {"name": "DD_TRACE_LOG_FILE_SIZE_BYTES", "origin": "default", "value": 15728640},
        {"name": "DD_TRACE_LOG_LEVEL", "origin": "default", "value": None},
        {"name": "DD_TRACE_LOG_STREAM_HANDLER", "origin": "default", "value": True},
        {"name": "DD_TRACE_METHODS", "origin": "default", "value": None},
        {"name": "DD_TRACE_NATIVE_SPAN_EVENTS", "origin": "default", "value": False},
        {"name": "DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP", "origin": "env_var", "value": ".*"},
        {"name": "DD_TRACE_OTEL_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_OTEL_SEMANTICS_ENABLED", "origin": "default", "value": False},
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
        {"name": "DD_TRACE_RESOURCE_RENAMING_ALWAYS_SIMPLIFIED_ENDPOINT", "origin": "default", "value": False},
        {"name": "DD_TRACE_RESOURCE_RENAMING_ENABLED", "origin": "default", "value": False},
        {"name": "DD_TRACE_SAFE_INSTRUMENTATION_ENABLED", "origin": "default", "value": False},
        {
            "name": "DD_TRACE_SAMPLING_RULES",
            "origin": "env_var",
            "value": '[{"sample_rate":1.0,"service":"xyz","name":"abc"}]',
        },
        {"name": "DD_TRACE_SPAN_TRACEBACK_MAX_SIZE", "origin": "default", "value": 30},
        {"name": "DD_TRACE_STARTUP_LOGS", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_STATS_COMPUTATION_ENABLED", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_WRAP_SPAN_NAME_INCLUDE_CLASS", "origin": "default", "value": False},
        {"name": "DD_TRACE_WRITER_BUFFER_SIZE_BYTES", "origin": "env_var", "value": 1000},
        {"name": "DD_TRACE_WRITER_INTERVAL_SECONDS", "origin": "env_var", "value": 30.0},
        {"name": "DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES", "origin": "env_var", "value": 9999},
        {"name": "DD_TRACE_WRITER_REUSE_CONNECTIONS", "origin": "env_var", "value": True},
        {"name": "DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH", "origin": "default", "value": 512},
        {"name": "DD_USER_MODEL_EMAIL_FIELD", "origin": "default", "value": ""},
        {"name": "DD_USER_MODEL_LOGIN_FIELD", "origin": "default", "value": ""},
        {"name": "DD_USER_MODEL_NAME_FIELD", "origin": "default", "value": ""},
        {"name": "DD_VERSION", "origin": "default", "value": None},
        {
            "name": "OTEL_EXPORTER_OTLP_ENDPOINT",
            "origin": "env_var",
            "value": "http://localhost:4317",
        },
        # OTEL_EXPORTER_OTLP_HEADERS is excluded from configuration telemetry.
        {
            "name": "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
            "origin": "default",
            "value": f"http://{get_agent_hostname()}:4317",
        },
        # OTEL_EXPORTER_OTLP_LOGS_HEADERS is excluded from configuration telemetry.
        {
            "name": "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
            "origin": "default",
            "value": "grpc",
        },
        {
            "name": "OTEL_EXPORTER_OTLP_LOGS_TIMEOUT",
            "origin": "default",
            "value": 10000,
        },
        {
            "name": "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
            "origin": "default",
            "value": f"http://{get_agent_hostname()}:4317",
        },
        # OTEL_EXPORTER_OTLP_METRICS_HEADERS is excluded from configuration telemetry.
        {
            "name": "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
            "origin": "default",
            "value": "grpc",
        },
        {
            "name": "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
            "origin": "default",
            "value": "delta",
        },
        {
            "name": "OTEL_EXPORTER_OTLP_METRICS_TIMEOUT",
            "origin": "default",
            "value": 10000,
        },
        {
            "name": "OTEL_EXPORTER_OTLP_PROTOCOL",
            "origin": "default",
            "value": "grpc",
        },
        {
            "name": "OTEL_EXPORTER_OTLP_TIMEOUT",
            "origin": "default",
            "value": 10000,
        },
        # OTEL_EXPORTER_OTLP_TRACES_HEADERS is excluded from configuration telemetry.
        {
            "name": "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
            "origin": "default",
            "value": "grpc",
        },
        {
            "name": "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT",
            "origin": "default",
            "value": 10000,
        },
        {
            "name": "OTEL_METRIC_EXPORT_INTERVAL",
            "origin": "default",
            "value": 10000,
        },
        {
            "name": "OTEL_METRIC_EXPORT_TIMEOUT",
            "origin": "default",
            "value": 7500,
        },
        {
            "name": "OTEL_TRACES_SPAN_METRICS_ENABLED",
            "origin": "default",
            "value": None,
        },
        {
            "name": "_DD_APM_TRACING_AGENTLESS_ENABLED",
            "origin": "default",
            "value": False,
        },
        {"name": "_DD_APPSEC_DEDUPLICATION_ENABLED", "origin": "default", "value": True},
        {"name": "_DD_IAST_LAZY_TAINT", "origin": "default", "value": False},
        {"name": "_DD_IAST_USE_ROOT_SPAN", "origin": "default", "value": False},
        {"name": "_DD_NATIVE_LOGGING_BACKEND", "origin": "default", "value": None},
        {
            "name": "_DD_TRACE_STATS_COMPUTATION_EXPERIMENTAL_CLIENT_OBFUSCATION_ENABLED",
            "origin": "default",
            "value": False,
        },
        {"name": "_DD_TRACE_WRITER_LOG_ERROR_PAYLOADS", "origin": "default", "value": False},
        {"name": "instrumentation_source", "origin": "code", "value": "manual"},
        {"name": "llmobs_oneclick_supported", "origin": "code", "value": False},
        {"name": "python_build_gnu_type", "origin": "unknown", "value": sysconfig.get_config_var("BUILD_GNU_TYPE")},
        {"name": "python_host_gnu_type", "origin": "unknown", "value": sysconfig.get_config_var("HOST_GNU_TYPE")},
        {"name": "python_soabi", "origin": "unknown", "value": sysconfig.get_config_var("SOABI")},
    ]
    # The native worker serializes every configuration value as a string, so normalize the
    # typed expected values to their wire form before comparing.
    for cfg in expected:
        cfg["value"] = _to_config_str(cfg["value"])
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


_MINI_DJANGO_APP = """
from os import path as osp
def rel_path(*p): return osp.normpath(osp.join(rel_path.path, *p))
rel_path.path = osp.abspath(osp.dirname(__file__))
this = osp.splitext(osp.basename(__file__))[0]
from django.conf import settings
SETTINGS = dict(
    DATABASES = {},
    DEBUG=True,
    TEMPLATE_DEBUG=True,
    ROOT_URLCONF = this
)
SETTINGS['DATABASES']={
    'default':{
        'ENGINE':'django.db.backends.sqlite3',
        'NAME':rel_path('db')
    }
}

if __name__=='__main__':
    settings.configure(**SETTINGS)

if __name__ == '__main__':
    %(bootstrap)s

from django.urls import path
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
@require_http_methods(["GET"])
def view_name(request):
    return HttpResponse('response text')
def mini_app(request):
    return HttpResponse('response text')
urlpatterns = [ path('mini_app/',mini_app), path('view_name/', view_name) ]
"""

# What gunicorn/uwsgi do: build the WSGI application, which constructs a BaseHandler.
_SERVING_BOOTSTRAP = """from django.core.wsgi import get_wsgi_application
    get_wsgi_application()"""

# What a Celery or dramatiq worker does: django.setup() and nothing else. Not a management
# command -- Django's own check_url_config imports the URLconf whenever system checks run, so
# most manage.py invocations import it with or without ddtrace.
_WORKER_BOOTSTRAP = """import django
    django.setup()
    assert this not in __import__('sys').modules, 'django.setup() imported the URLconf'"""


def test_endpoint_discovery_event(test_agent_session, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    # app-started events are sent 10 seconds after ddtrace imported, this configuration overrides this
    # behavior to force the app-started event to be queued immediately
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    mini_django_app = _MINI_DJANGO_APP % {"bootstrap": _SERVING_BOOTSTRAP}

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(mini_django_app, env=env)
    assert status == 0, stderr
    deps = test_agent_session.get_dependencies("django")
    assert len(deps) == 1, deps

    events = test_agent_session.get_events("app-endpoints")
    assert len(events) == 1, events
    payload = events[0]["payload"]
    assert payload["is-first"] is True
    endpoints = payload["endpoints"]
    assert len(endpoints) == 2, endpoints
    # The mini_app view has no @require_http_methods, so its method is unknown/unconstrained.
    # libdatadog's ``Method::Other`` serializes to "*" (the value the app-endpoints OpenAPI spec
    # uses for the any-method concept), matching the old Python writer.
    assert any(
        e["path"] == "mini_app/" and e["method"] == "*" and e["operation_name"] == "django.request" for e in endpoints
    ), endpoints
    assert any(
        e["path"] == "view_name/"
        and e["method"] == "GET"
        and e["resource_name"] == "GET view_name/"
        and e["operation_name"] == "django.request"
        for e in endpoints
    ), endpoints


def test_endpoint_discovery_skipped_without_http_handler(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """A process that never builds a request handler must not import the URLconf.

    Reading resolver.url_patterns imports ROOT_URLCONF and, through include(), every view module behind it. A Celery
    or dramatiq worker would otherwise load that whole import closure for nothing, which cost one reporter 154MB of
    RSS per worker.
    """
    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    mini_django_app = _MINI_DJANGO_APP % {"bootstrap": _WORKER_BOOTSTRAP}

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(mini_django_app, env=env)
    assert status == 0, stderr
    deps = test_agent_session.get_dependencies("django")
    assert len(deps) == 1, deps

    assert test_agent_session.get_events("app-endpoints") == []


def test_instrumentation_source_config(
    test_agent_session, ddtrace_run_python_code_in_subprocess, run_python_code_in_subprocess
):
    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = call_program("ddtrace-run", sys.executable, "-c", "", env=env)
    assert status == 0, stderr
    configs = test_agent_session.get_configurations("instrumentation_source")
    assert configs and configs[-1]["value"] == "cmd_line"
    test_agent_session.clear()

    _, stderr, status, _ = call_program(sys.executable, "-c", "import ddtrace.auto", env=env)
    assert status == 0, stderr
    configs = test_agent_session.get_configurations("instrumentation_source")
    assert configs and configs[-1]["value"] == "manual"
    test_agent_session.clear()

    _, stderr, status, _ = call_program(sys.executable, "-c", "import ddtrace", env=env)
    assert status == 0, stderr
    configs = test_agent_session.get_configurations("instrumentation_source")
    assert not configs, "instrumentation_source should not be set when ddtrace instrumentation is not used"


def test_update_dependencies_event_when_disabled(test_agent_session, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    # app-started events are sent 10 seconds after ddtrace imported, this configuration overrides this
    # behavior to force the app-started event to be queued immediately
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"
    env["DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED"] = "false"

    # Import httppretty after ddtrace is imported, this ensures that the module is sent in a dependencies event
    # Imports httpretty twice and ensures only one dependency entry is sent
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess("import xmltodict", env=env)
    events = test_agent_session.get_events("app-dependencies-loaded")
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
    # Telemetry writer must start before app-closing event is queued
    telemetry_writer.started = True
    # send app closed event
    telemetry_writer.app_shutdown()
    # ensure a valid app-closing request body was sent. The native worker's shutdown/rebuild
    # lifecycle (incl. the test-session token rebuild) may surface more than one app-closing, so
    # assert at least one was sent and that it has a valid body. The app-closing unit payload
    # serializes with no "payload" key (the harness defaults it to {}), and seq_id is owned by the
    # native worker, so we don't pin it.
    events = test_agent_session.get_events("app-closing")
    assert len(events) >= 1
    validate_request_body(events[0], {}, "app-closing")


def test_add_integration(telemetry_writer, test_agent_session, mock_time):
    """asserts that add_integration() queues a valid telemetry request"""
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        # queue integrations
        telemetry_writer.add_integration("integration-t", True, True, "")
        telemetry_writer.add_integration("integration-f", False, False, "terrible failure")
        # send integrations to the agent
        telemetry_writer.periodic(force_flush=True)

        events = test_agent_session.get_events("app-integrations-change")
        # assert integration change telemetry request was sent
        assert len(events) == 1
        # assert that the request had a valid request body
        events[0]["payload"]["integrations"].sort(key=lambda x: x["name"])
        # The native Integration payload has no ``error`` field (the error string is only used to
        # derive ``compatible``) and ``version`` serializes as ``null`` when empty.
        expected_payload = {
            "integrations": [
                {
                    "name": "integration-f",
                    "version": None,
                    "enabled": False,
                    "auto_enabled": False,
                    "compatible": False,
                },
                {
                    "name": "integration-t",
                    "version": None,
                    "enabled": True,
                    "auto_enabled": True,
                    "compatible": True,
                },
            ]
        }
        validate_request_body(events[0], expected_payload, "app-integrations-change")


def test_app_client_configuration_changed_event(telemetry_writer, test_agent_session, mock_time):
    # force periodic call to flush the first app_started call
    telemetry_writer.periodic(force_flush=True)
    """asserts that queuing a configuration sends a valid telemetry request"""
    with override_global_config(dict()):
        telemetry_writer.add_configuration("product_enabled", True, "env_var")
        telemetry_writer.add_configuration("DD_TRACE_PROPAGATION_STYLE_EXTRACT", "datadog", "default")
        telemetry_writer.add_configuration("product_enabled", False, "code")

        telemetry_writer.periodic(force_flush=True)

        events = test_agent_session.get_events("app-client-configuration-change")
        received_configurations = [c for event in events for c in event["payload"]["configuration"]]
        received_configurations.sort(key=lambda c: c["seq_id"])
        assert (
            received_configurations[0]["seq_id"]
            < received_configurations[1]["seq_id"]
            < received_configurations[2]["seq_id"]
        )
        # assert that all configuration values are sent to the agent in the order they were added
        # (by seq_id). The native worker serializes every config value as a string.
        assert received_configurations[0]["name"] == "product_enabled"
        assert received_configurations[0]["origin"] == "env_var"
        assert received_configurations[0]["value"] == "true"
        assert received_configurations[1]["name"] == "DD_TRACE_PROPAGATION_STYLE_EXTRACT"
        assert received_configurations[1]["origin"] == "default"
        assert received_configurations[1]["value"] == "datadog"
        assert received_configurations[2]["name"] == "product_enabled"
        assert received_configurations[2]["origin"] == "code"
        assert received_configurations[2]["value"] == "false"


def test_add_integration_disabled_writer(telemetry_writer, test_agent_session):
    """asserts that add_integration() does not queue an integration when telemetry is disabled"""
    telemetry_writer.disable()

    telemetry_writer.add_integration("integration-name", True, False, "")
    telemetry_writer.periodic(force_flush=True)
    assert len(test_agent_session.get_events("app-integrations-change")) == 0


# NOTE: ``test_send_failing_request`` was removed. It exercised Python-side HTTP retry/error
# logging via httpretty + ``telemetry_writer._client``. Transport (including failure handling
# and logging of unsuccessful responses) now lives in the libdd-telemetry Rust crate, so it can
# no longer be intercepted by httpretty from Python and is covered on the native side.

# NOTE: ``test_app_heartbeat_event_periodic`` was removed. It exercised the Python-side
# heartbeat-gating counters (``_is_periodic`` / ``interval`` / ``_periodic_threshold`` /
# ``_periodic_count``), which no longer exist — the native worker self-schedules heartbeats.
# ``test_app_heartbeat_event`` below still covers that heartbeats are emitted.


def test_app_heartbeat_event(mock_time: mock.Mock, telemetry_writer: Any, test_agent_session: Any) -> None:
    """asserts that we queue/send app-heartbeat event every 60 seconds when app_heartbeat_event() is called"""
    # Assert a maximum of one heartbeat is queued per flush
    telemetry_writer.periodic(force_flush=True)
    events = test_agent_session.get_events(mock.ANY, filter_heartbeats=False)
    assert len(events) > 0


def test_app_product_change_event(mock_time: mock.Mock, telemetry_writer: Any, test_agent_session: Any) -> None:
    """asserts that enabling or disabling an APM Product triggers a valid telemetry request"""

    # Product enablement state is now tracked inside the native worker and asserted via the
    # emitted ``app-product-change`` events below (the Python-side ``_product_enablement`` dict
    # is gone).
    #
    # The native worker emits ``app-started`` eagerly on start() (before these activations), so
    # unlike the old Python writer it does NOT fold product activations into app-started. Each
    # set of product status changes is emitted as its own ``app-product-change`` event; an
    # activation that does not change a product's status produces no event.
    version = _pep440_to_semver()

    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.LLMOBS, True)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION, True)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, True)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, True)

    telemetry_writer.periodic(force_flush=True)

    # The four activations are emitted as app-product-change (app-started already fired eagerly).
    events = test_agent_session.get_events("app-product-change")
    assert len(events) == 1, events
    products = events[0]["payload"]["products"]
    assert products == {
        TELEMETRY_APM_PRODUCT.LLMOBS.value: {"enabled": True, "version": version, "error": None},
        TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION.value: {"enabled": True, "version": version, "error": None},
        TELEMETRY_APM_PRODUCT.PROFILER.value: {"enabled": True, "version": version, "error": None},
        TELEMETRY_APM_PRODUCT.APPSEC.value: {"enabled": True, "version": version, "error": None},
    }
    test_agent_session.clear()

    # The native worker marks a product pending on every ``product_activated`` call (it does not
    # diff against the previous status), so re-activating an already-enabled product re-emits it.
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, True)
    telemetry_writer.periodic(force_flush=True)
    events = test_agent_session.get_events("app-product-change")
    assert len(events) == 1
    assert events[0]["payload"]["products"] == {
        TELEMETRY_APM_PRODUCT.PROFILER.value: {"enabled": True, "version": version, "error": None},
    }
    test_agent_session.clear()

    # Assert that product change event is sent when product status changes
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, False)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION, False)
    telemetry_writer.periodic(force_flush=True)
    events = test_agent_session.get_events("app-product-change")
    assert len(events) == 1
    assert events[0]["request_type"] == "app-product-change"
    products = events[0]["payload"]["products"]
    assert products == {
        TELEMETRY_APM_PRODUCT.APPSEC.value: {"enabled": False, "version": version, "error": None},
        TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION.value: {"enabled": False, "version": version, "error": None},
    }


def validate_request_body(received_body: dict, payload: dict, payload_type: str, seq_id: Optional[int] = None) -> dict:
    """used to test the body of requests received by the testagent"""
    # The native worker serializes a fixed set of 8 top-level keys. Unlike the old Python
    # body, there is no ``debug`` key anymore.
    assert set(received_body.keys()) == {
        "api_version",
        "tracer_time",
        "runtime_id",
        "seq_id",
        "application",
        "host",
        "request_type",
        "payload",
    }
    # tracer_time is stamped by the native worker (Rust), so it cannot be mocked from
    # Python (mock_time) — just sanity-check it is a positive epoch-seconds integer.
    assert isinstance(received_body["tracer_time"], int) and received_body["tracer_time"] > 0
    assert received_body["runtime_id"] == get_runtime_id()
    assert received_body["api_version"] == "v2"
    if seq_id is not None:
        assert received_body["seq_id"] == seq_id
    # The wire body omits empty/None application + host fields (serde skip_serializing_if),
    # so only compare against the fields actually present in the received body.
    expected_application = get_application(config.service, config.version, config.env)
    assert received_body["application"] == {
        k: v for k, v in expected_application.items() if k in received_body["application"]
    }
    expected_host = get_host_info()
    assert received_body["host"] == {k: v for k, v in expected_host.items() if k in received_body["host"]}
    if payload is not None:
        assert received_body["payload"] == payload
    assert received_body["request_type"] == payload_type
    return received_body


def test_telemetry_writer_agent_setup():
    with override_global_config(
        {"_dd_site": "datad0g.com", "_dd_api_key": "foobarkey", "_ci_visibility_agentless_enabled": False}
    ):
        new_telemetry_writer = ddtrace.internal.telemetry.TelemetryWriter(agentless=False)
        # Transport now lives in the native worker; the Python-visible decision is the
        # ``_agentless`` flag. Agent mode -> _agentless is False (telemetry POSTed to the
        # trace agent proxy by the native worker).
        assert new_telemetry_writer._enabled
        assert new_telemetry_writer._agentless is False


@pytest.mark.parametrize(
    "env_agentless,arg_agentless",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_telemetry_writer_agent_setup_agentless_arg_overrides_env(env_agentless, arg_agentless):
    with override_global_config(
        {"_dd_site": "datad0g.com", "_dd_api_key": "foobarkey", "_ci_visibility_agentless_enabled": env_agentless}
    ):
        new_telemetry_writer = ddtrace.internal.telemetry.TelemetryWriter(agentless=arg_agentless)
        # The explicit ``agentless`` argument always wins over the env-derived value.
        assert new_telemetry_writer._agentless is arg_agentless


@pytest.mark.subprocess(
    env={"DD_SITE": "datad0g.com", "DD_API_KEY": "foobarkey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
)
def test_telemetry_writer_agentless_setup():
    from ddtrace import config
    from ddtrace.internal.telemetry import telemetry_writer
    from ddtrace.internal.telemetry.writer import _agentless_endpoint_url

    assert telemetry_writer._enabled
    assert telemetry_writer._agentless is True
    # The api key is now applied as a header inside the native worker; assert via config.
    assert config._dd_api_key == "foobarkey"
    assert _agentless_endpoint_url(config._dd_site) == "https://all-http-intake.logs.datad0g.com"


@pytest.mark.subprocess(
    env={"DD_SITE": "datadoghq.eu", "DD_API_KEY": "foobarkey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
)
def test_telemetry_writer_agentless_setup_eu():
    from ddtrace import config
    from ddtrace.internal.telemetry import telemetry_writer
    from ddtrace.internal.telemetry.writer import _agentless_endpoint_url

    assert telemetry_writer._enabled
    assert telemetry_writer._agentless is True
    assert config._dd_api_key == "foobarkey"
    assert _agentless_endpoint_url(config._dd_site) == "https://instrumentation-telemetry-intake.datadoghq.eu"


@pytest.mark.subprocess(env={"DD_SITE": "datad0g.com", "DD_API_KEY": "", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"})
def test_telemetry_writer_agentless_disabled_without_api_key():
    from ddtrace import config
    from ddtrace.internal.telemetry import telemetry_writer

    # Agentless requested but no api key -> telemetry is disabled.
    assert not telemetry_writer._enabled
    assert telemetry_writer._agentless is True
    assert config._dd_api_key in (None, "")


@pytest.mark.subprocess(env={"DD_SITE": "datad0g.com", "DD_API_KEY": "foobarkey"})
def test_telemetry_writer_is_using_agentless_by_default_if_api_key_is_available():
    from ddtrace import config
    from ddtrace.internal.telemetry import telemetry_writer
    from ddtrace.internal.telemetry.writer import _agentless_endpoint_url

    # When an api key is present (and agentless not explicitly disabled) the writer defaults
    # to agentless mode.
    assert telemetry_writer._enabled
    assert telemetry_writer._agentless is True
    assert config._dd_api_key == "foobarkey"
    assert _agentless_endpoint_url(config._dd_site) == "https://all-http-intake.logs.datad0g.com"


@pytest.mark.subprocess(env={"DD_API_KEY": "", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "false"})
def test_telemetry_writer_is_using_agent_by_default_if_api_key_is_not_available():
    from ddtrace import config
    from ddtrace.internal.telemetry import telemetry_writer

    # No api key and agentless disabled -> agent mode (telemetry goes to the trace agent).
    assert telemetry_writer._enabled
    assert telemetry_writer._agentless is False
    assert config._dd_api_key in (None, "")


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
    env["OTEL_METRICS_EXPORTER"] = "otlp"
    env["OTEL_RESOURCE_ATTRIBUTES"] = "team=apm,component=web"
    env["OTEL_SDK_DISABLED"] = "true"
    env["OTEL_UNSUPPORTED_CONFIG"] = "value"
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace", env=env)
    assert status == 0, stderr

    configurations = {c["name"]: c for c in test_agent_session.get_configurations(remove_seq_id=True, effective=True)}

    assert configurations["DD_SERVICE"] == {"name": "DD_SERVICE", "origin": "env_var", "value": "dd_service"}
    assert configurations["DD_TRACE_DEBUG"] == {"name": "DD_TRACE_DEBUG", "origin": "otel_env_var", "value": "debug"}
    assert configurations["DD_TRACE_PROPAGATION_STYLE_INJECT"] == {
        "name": "DD_TRACE_PROPAGATION_STYLE_INJECT",
        "origin": "otel_env_var",
        "value": "tracecontext",
    }
    assert configurations["DD_TRACE_PROPAGATION_STYLE_EXTRACT"] == {
        "name": "DD_TRACE_PROPAGATION_STYLE_EXTRACT",
        "origin": "otel_env_var",
        "value": "tracecontext",
    }
    assert configurations["DD_TRACE_SAMPLING_RULES"] == {
        "name": "DD_TRACE_SAMPLING_RULES",
        "origin": "otel_env_var",
        "value": "always_on",
    }
    assert configurations["DD_TRACE_ENABLED"] == {
        "name": "DD_TRACE_ENABLED",
        "origin": "otel_env_var",
        "value": "none",
    }
    assert configurations["DD_TAGS"] == {
        "name": "DD_TAGS",
        "origin": "otel_env_var",
        "value": "team=apm,component=web",
    }
    assert configurations["DD_TRACE_OTEL_ENABLED"] == {
        "name": "DD_TRACE_OTEL_ENABLED",
        "origin": "otel_env_var",
        "value": "true",
    }

    env_hiding_metrics = test_agent_session.get_metrics("otel.env.hiding")
    tags = [m["tags"] for m in env_hiding_metrics]
    assert tags == [["config_opentelemetry:otel_service_name", "config_datadog:dd_service"]]

    env_unsupported_metrics = test_agent_session.get_metrics("otel.env.unsupported")
    tags = [m["tags"] for m in env_unsupported_metrics]
    assert tags == [["config_opentelemetry:otel_unsupported_config"]]

    env_invalid_metrics = test_agent_session.get_metrics("otel.env.invalid")
    tags = [m["tags"] for m in env_invalid_metrics]
    assert tags == [["config_opentelemetry:otel_logs_exporter"]]


def test_otel_exporter_otlp_headers_telemetry_omitted(test_agent_session, run_python_code_in_subprocess):
    """The OTEL_EXPORTER_OTLP_*_HEADERS family is excluded from configuration telemetry, while
    non-sensitive OTLP exporter configurations are still reported.
    """
    code = """
# most configurations are reported when ddtrace.auto is imported
import ddtrace.auto
# importing opentelemetry triggers reporting of the OTLP exporter configurations
import opentelemetry
    """

    # Distinct, recognizable sentinels per OTLP header variant.
    sentinels = [
        "SENTINEL_OTLP_BASE",
        "SENTINEL_OTLP_TRACES",
        "SENTINEL_OTLP_METRICS",
        "SENTINEL_OTLP_LOGS",
    ]

    env = os.environ.copy()
    env["OTEL_EXPORTER_OTLP_HEADERS"] = "dd-api-key=SENTINEL_OTLP_BASE"
    env["OTEL_EXPORTER_OTLP_TRACES_HEADERS"] = "dd-api-key=SENTINEL_OTLP_TRACES"
    env["OTEL_EXPORTER_OTLP_METRICS_HEADERS"] = "dd-api-key=SENTINEL_OTLP_METRICS"
    env["OTEL_EXPORTER_OTLP_LOGS_HEADERS"] = "dd-api-key=SENTINEL_OTLP_LOGS"
    # Non-sensitive OTLP exporter configurations that must still be reported.
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4318"
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    configurations = {c["name"]: c for c in test_agent_session.get_configurations(remove_seq_id=True, effective=True)}
    assert configurations, "no configuration telemetry was reported"

    # Invariant: no OTLP header sentinel appears in any reported configuration value.
    for cfg in configurations.values():
        for sentinel in sentinels:
            assert sentinel not in str(cfg["value"]), cfg

    # Python omits the OTLP header family entirely.
    for name in (
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
        "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
    ):
        assert name not in configurations, configurations.get(name)

    # Non-sensitive OTLP exporter configurations are still reported.
    assert configurations["OTEL_EXPORTER_OTLP_ENDPOINT"] == {
        "name": "OTEL_EXPORTER_OTLP_ENDPOINT",
        "origin": "env_var",
        "value": "http://localhost:4318",
    }
    # Sibling non-sensitive exporter configs (collected at import) remain present.
    assert "OTEL_EXPORTER_OTLP_PROTOCOL" in configurations
    assert "OTEL_EXPORTER_OTLP_TIMEOUT" in configurations


def test_dd_api_key_app_key_telemetry_omitted(telemetry_writer, test_agent_session):
    """DD_API_KEY and DD_APP_KEY values are excluded from configuration telemetry.

    Uses the in-process telemetry writer (forced non-agentless) because setting DD_API_KEY would
    otherwise switch a subprocess's telemetry client into agentless mode and divert it from the
    test agent.
    """
    from ddtrace.internal.telemetry import get_config

    with mock.patch.dict(
        os.environ,
        {"DD_API_KEY": "SENTINEL_DD_API_KEY", "DD_APP_KEY": "SENTINEL_DD_APP_KEY"},
    ):
        # Read each sensitive key the way settings do; the value must not be reported via telemetry.
        assert get_config("DD_API_KEY") == "SENTINEL_DD_API_KEY"
        assert get_config("DD_APP_KEY") == "SENTINEL_DD_APP_KEY"
        # A non-sensitive control config is still reported, proving reporting is otherwise active.
        get_config("DD_SITE", "datadoghq.com")

    # Flush the queued configurations to the native worker -> test agent.
    telemetry_writer.periodic(force_flush=True)

    configurations = test_agent_session.get_configurations()
    reported_names = {c["name"] for c in configurations}
    assert "DD_API_KEY" not in reported_names, configurations
    assert "DD_APP_KEY" not in reported_names, configurations
    for cfg in configurations:
        assert "SENTINEL_DD_API_KEY" not in str(cfg["value"]), cfg
        assert "SENTINEL_DD_APP_KEY" not in str(cfg["value"]), cfg
    # Sanity check: the non-sensitive control config was reported.
    assert "DD_SITE" in reported_names, configurations


def test_add_error_log(mock_time, telemetry_writer, test_agent_session):
    """Test add_integration_error_log functionality with real stack trace"""
    try:
        import json

        json.loads("{invalid: json,}")
    except Exception as e:
        telemetry_writer.add_error_log("Test error message", e)
        telemetry_writer.periodic(force_flush=True)

        log_events = test_agent_session.get_events("logs")
        assert len(log_events) == 1

        logs = log_events[0]["payload"]["logs"]
        assert len(logs) == 1

        log_entry = logs[0]
        assert log_entry["level"] == TELEMETRY_LOG_LEVEL.ERROR.value
        assert log_entry["message"] == "Test error message"
        assert log_entry["tags"] == "error_type:jsondecodeerror"

        stack_trace = log_entry["stack_trace"]
        expected_lines = [
            "Traceback (most recent call last):",
            "<REDACTED>",  # User code gets redacted
            '  File "json/__init__.py',
            "    return _default_decoder.decode(s)",
            '  File "json/decoder.py"',
            "    obj, end = self.raw_decode(s, idx=_w(s, 0).end())",
            '  File "json/decoder.py"',
            "    obj, end = self.scan_once(s, idx)",
            "json.decoder.JSONDecodeError: <REDACTED>",
        ]
        for expected_line in expected_lines:
            assert expected_line in stack_trace


def test_add_error_log_large_stack(mock_time, telemetry_writer, test_agent_session):
    """Test add_integration_error_log functionality with real stack trace"""
    try:

        def _(n):
            if n == 200:
                raise ValueError("Test exception for large stack trace")
            return _(n + 1)

        _(0)
    except Exception as e:
        telemetry_writer.add_error_log("Test error message", e)
        telemetry_writer.periodic(force_flush=True)

        log_events = test_agent_session.get_events("logs")
        assert len(log_events) == 1

        logs = log_events[0]["payload"]["logs"]
        assert len(logs) == 1

        log_entry = logs[0]
        assert log_entry["level"] == TELEMETRY_LOG_LEVEL.ERROR.value
        assert log_entry["message"] == "Test error message"
        assert log_entry["tags"] == "error_type:valueerror"

        stack_trace = log_entry["stack_trace"]
        expected_lines = """Traceback (most recent call last):
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
builtins.ValueError: <REDACTED>"""
        assert stack_trace == expected_lines


def test_add_integration_error_log_with_log_collection_disabled(mock_time, telemetry_writer, test_agent_session):
    """Test that add_integration_error_log respects LOG_COLLECTION_ENABLED setting"""
    original_value = telemetry_config.LOG_COLLECTION_ENABLED
    try:
        telemetry_config.LOG_COLLECTION_ENABLED = False

        try:
            raise ValueError("Test exception")
        except ValueError as e:
            telemetry_writer.add_error_log("Test error message", e)
            telemetry_writer.periodic(force_flush=True)

            log_events = test_agent_session.get_events("logs")
            assert len(log_events) == 0
    finally:
        telemetry_config.LOG_COLLECTION_ENABLED = original_value


def test_error_log_handler_strips_skipped_suffix(mock_time, telemetry_writer, test_agent_session):
    """Test that DDTelemetryErrorHandler strips [x skipped] suffix from error messages"""
    import logging

    ddtrace_logger = logging.getLogger("ddtrace")

    ddtrace_logger.error("Error message [123 skipped]")
    telemetry_writer.periodic(force_flush=True)

    log_events = test_agent_session.get_events("logs")
    assert len(log_events) == 1

    logs = log_events[0]["payload"]["logs"]
    assert len(logs) == 1
    assert logs[0]["message"] == "Error message"

    test_agent_session.clear()

    ddtrace_logger.error("Normal error message [something]")
    telemetry_writer.periodic(force_flush=True)

    log_events = test_agent_session.get_events("logs")
    assert len(log_events) == 1

    logs = log_events[0]["payload"]["logs"]
    assert len(logs) == 1
    assert logs[0]["message"] == "Normal error message [something]"


@pytest.mark.parametrize(
    "filename, result",
    [
        ("/path/to/file.py", "<REDACTED>"),
        ("/path/to/ddtrace/contrib/flask/file.py", "<REDACTED>"),
        ("/path/to/lib/python3.13/site-packages/ddtrace/_trace/tracer.py", "ddtrace/_trace/tracer.py"),
        ("/path/to/lib/python3.13/site-packages/requests/api.py", "requests/api.py"),
        (
            "/path/to/python@3.13/3.13.1/Frameworks/Python.framework/Versions/3.13/lib/python3.13/json/__init__.py",
            "json/__init__.py",
        ),
    ],
)
def test_redact_filename(filename, result):
    """Test file redaction logic"""
    writer = TelemetryWriter()
    assert writer._format_file_path(filename) == result


def test_endpoint_subscription_lifecycle(telemetry_writer):
    """``enable`` subscribes the writer to the endpoint collection, ``disable`` unsubscribes it."""
    from ddtrace.internal.endpoints import endpoint_collection

    assert endpoint_collection.on_endpoint_registered == telemetry_writer._record_endpoint

    telemetry_writer.disable()
    assert endpoint_collection.on_endpoint_registered is None


def test_disable_leaves_a_foreign_endpoint_subscriber_alone(telemetry_writer):
    """Only the writer's own subscription is cleared, so a disable cannot unhook someone else."""
    from ddtrace.internal.endpoints import endpoint_collection

    def other(endpoint):
        pass

    endpoint_collection.on_endpoint_registered = other
    telemetry_writer.disable()

    assert endpoint_collection.on_endpoint_registered is other


def test_telemetry_writer_multiple_sources_config(telemetry_writer, test_agent_session):
    """Test that telemetry data is submitted for multiple sources with increasing seq_id"""

    telemetry_writer.add_configuration("DD_SERVICE", "unamed_python_service", "default")
    telemetry_writer.add_configuration("DD_SERVICE", "otel_service", "otel_env_var")
    telemetry_writer.add_configuration("DD_SERVICE", "dd_service", "env_var")
    telemetry_writer.add_configuration("DD_SERVICE", "monkey", "code")
    telemetry_writer.add_configuration("DD_SERVICE", "baboon", "remote_config")
    telemetry_writer.add_configuration("DD_SERVICE", "baboon", "fleet_stable_config")

    telemetry_writer.periodic(force_flush=True)

    configs = test_agent_session.get_configurations(name="DD_SERVICE", remove_seq_id=False, effective=False)
    assert len(configs) == 6, configs

    sorted_configs = sorted(configs, key=lambda x: x["seq_id"])
    # The native worker owns the configuration seq_id and stamps the eagerly-reported
    # ``python_*`` configs first, so absolute seq_ids are offset. Assert the relative order
    # (each source increments the seq_id, in insertion order) instead of absolute values.
    seq_ids = [c["seq_id"] for c in sorted_configs]
    assert seq_ids == sorted(seq_ids) and len(set(seq_ids)) == 6, seq_ids

    assert sorted_configs[0]["value"] == "unamed_python_service"
    assert sorted_configs[0]["origin"] == "default"

    assert sorted_configs[1]["value"] == "otel_service"
    assert sorted_configs[1]["origin"] == "otel_env_var"

    assert sorted_configs[2]["value"] == "dd_service"
    assert sorted_configs[2]["origin"] == "env_var"

    assert sorted_configs[3]["value"] == "monkey"
    assert sorted_configs[3]["origin"] == "code"

    assert sorted_configs[4]["value"] == "baboon"
    assert sorted_configs[4]["origin"] == "remote_config"

    assert sorted_configs[5]["value"] == "baboon"
    assert sorted_configs[5]["origin"] == "fleet_stable_config"


def test_report_configuration_walks_ddconfig(telemetry_writer, test_agent_session, monkeypatch):
    """report_configuration() reports every public, non-sensitive item of a DDConfig with its
    resolved value, source and config_id, and skips private and sensitive items entirely.
    """
    monkeypatch.setenv("DD_TEST_SYNTHETIC_PUBLIC_SETTING", "from_env")
    monkeypatch.setenv("DD_TEST_SYNTHETIC_BOOL_SETTING", "true")
    monkeypatch.setenv("DD_TEST_SYNTHETIC_FLOAT_SETTING", "1.5")

    with (
        mock.patch.dict(settings_core.FLEET_CONFIG, {"DD_TEST_SYNTHETIC_FLEET_SETTING": "from_fleet"}),
        mock.patch.dict(settings_core.FLEET_CONFIG_IDS, {"DD_TEST_SYNTHETIC_FLEET_SETTING": "config-id-123"}),
    ):
        synthetic_config = _SyntheticDDConfig()

    with mock.patch.object(
        ddtrace.internal.telemetry,
        "SENSITIVE_CONFIGURATIONS",
        frozenset({"DD_TEST_SYNTHETIC_SENSITIVE_SETTING"}),
    ):
        ddtrace.internal.telemetry.report_configuration(synthetic_config)

    telemetry_writer.periodic(force_flush=True)
    reported = {c["name"]: c for c in test_agent_session.get_configurations(remove_seq_id=True)}

    assert reported["DD_TEST_SYNTHETIC_PUBLIC_SETTING"]["origin"] == "env_var"
    assert reported["DD_TEST_SYNTHETIC_PUBLIC_SETTING"]["value"] == "from_env"

    assert reported["DD_TEST_SYNTHETIC_FLEET_SETTING"]["origin"] == "fleet_stable_config"
    assert reported["DD_TEST_SYNTHETIC_FLEET_SETTING"]["value"] == "from_fleet"
    assert reported["DD_TEST_SYNTHETIC_FLEET_SETTING"]["config_id"] == "config-id-123"

    assert "DD_TEST_SYNTHETIC_PRIVATE_SETTING" not in reported
    assert "DD_TEST_SYNTHETIC_SENSITIVE_SETTING" not in reported

    # The native worker serializes every configuration value as a string, so compare against
    # the wire form (see _to_config_str) rather than the DDConfig item's declared type.
    assert reported["DD_TEST_SYNTHETIC_BOOL_SETTING"]["value"] == _to_config_str(True)
    assert reported["DD_TEST_SYNTHETIC_FLOAT_SETTING"]["value"] == _to_config_str(1.5)


def test_get_config_reports_all_sources_by_precedence(telemetry_writer, test_agent_session, monkeypatch):
    """get_config() reports telemetry for every source that supplies a value and returns the
    value from the highest-precedence source: fleet stable config > env var > local stable
    config > default.
    """
    name = "DD_TEST_SYNTHETIC_GET_CONFIG_SETTING"

    assert ddtrace.internal.telemetry.get_config(name, "default_value") == "default_value"

    with mock.patch.dict(ddtrace.internal.telemetry.LOCAL_CONFIG, {name: "local_value"}):
        assert ddtrace.internal.telemetry.get_config(name, "default_value") == "local_value"

    monkeypatch.setenv(name, "env_value")
    with mock.patch.dict(ddtrace.internal.telemetry.LOCAL_CONFIG, {name: "local_value"}):
        assert ddtrace.internal.telemetry.get_config(name, "default_value") == "env_value"

    with (
        mock.patch.dict(ddtrace.internal.telemetry.LOCAL_CONFIG, {name: "local_value"}),
        mock.patch.dict(ddtrace.internal.telemetry.FLEET_CONFIG, {name: "fleet_value"}),
        mock.patch.dict(ddtrace.internal.telemetry.FLEET_CONFIG_IDS, {name: "config-id-456"}),
    ):
        assert ddtrace.internal.telemetry.get_config(name, "default_value") == "fleet_value"

    telemetry_writer.periodic(force_flush=True)
    reported = test_agent_session.get_configurations(name=name, remove_seq_id=False, effective=False)
    origins = {c["origin"] for c in reported}
    assert origins == {"default", "local_stable_config", "env_var", "fleet_stable_config"}

    fleet_entry = next(c for c in reported if c["origin"] == "fleet_stable_config")
    assert fleet_entry["value"] == "fleet_value"
    assert fleet_entry["config_id"] == "config-id-456"


def test_get_config_respects_aliases_and_sensitive_configurations(telemetry_writer, test_agent_session, monkeypatch):
    """get_config() honors registered aliases of the canonical env var name and never reports
    telemetry for configurations marked sensitive, regardless of which source supplies them.
    """
    canonical = "DD_TEST_SYNTHETIC_CANONICAL_SETTING"
    alias = "DD_TEST_SYNTHETIC_LEGACY_ALIAS"
    monkeypatch.setenv(alias, "aliased_value")

    with mock.patch.dict(ddtrace.internal.telemetry.CONFIGURATION_ALIASES, {canonical: [alias]}):
        assert ddtrace.internal.telemetry.get_config(canonical, "default_value") == "aliased_value"

    telemetry_writer.periodic(force_flush=True)
    reported = {c["name"]: c for c in test_agent_session.get_configurations(remove_seq_id=True)}
    assert reported[canonical]["origin"] == "env_var"
    assert reported[canonical]["value"] == "aliased_value"

    sensitive_name = "DD_TEST_SYNTHETIC_SENSITIVE_GET_CONFIG_SETTING"
    monkeypatch.setenv(sensitive_name, "leaked_value")
    with mock.patch.object(
        ddtrace.internal.telemetry,
        "SENSITIVE_CONFIGURATIONS",
        frozenset({sensitive_name}),
    ):
        assert ddtrace.internal.telemetry.get_config(sensitive_name, "default_value") == "leaked_value"

    telemetry_writer.periodic(force_flush=True)
    reported = {c["name"]: c for c in test_agent_session.get_configurations(remove_seq_id=True)}
    assert sensitive_name not in reported


# err=None: with telemetry debug enabled the native worker logs its actions to stderr, which is
# expected here, so the default "no stderr" check must be relaxed.
@pytest.mark.subprocess(env={"DD_INTERNAL_TELEMETRY_DEBUG_ENABLED": "true"}, err=None)
def test_telemetry_debug_enabled_by_telemetry_env_var():
    """Telemetry debug mode is enabled only by DD_INTERNAL_TELEMETRY_DEBUG_ENABLED, not DD_TRACE_DEBUG."""
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._debug is True


@pytest.mark.subprocess(env={"DD_TRACE_DEBUG": "true"}, err=None)
def test_telemetry_debug_not_enabled_by_tracer_debug():
    """Setting DD_TRACE_DEBUG must not enable telemetry debug mode."""
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._debug is False
