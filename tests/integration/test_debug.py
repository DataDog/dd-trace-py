from datetime import datetime
import json
import logging
import os
import re
import subprocess
import sys
from typing import List
from typing import Optional

import mock
import pytest

import ddtrace
from ddtrace import Span
from ddtrace.internal import debug
from ddtrace.internal.compat import PY2
from ddtrace.internal.compat import PY3
from ddtrace.internal.writer import TraceWriter
import ddtrace.sampler
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess

from .test_integration import AGENT_VERSION


pytestmark = pytest.mark.skipif(AGENT_VERSION == "testagent", reason="The test agent doesn't support startup logs.")


def re_matcher(pattern):
    pattern = re.compile(pattern)

    class Match:
        def __eq__(self, other):
            return pattern.match(other)

    return Match()


def test_standard_tags():
    f = debug.collect(ddtrace.tracer)

    date = f.get("date")
    assert isinstance(date, str)

    if sys.version_info >= (3, 7, 0):
        # Try to parse the date-time, only built-in way to parse
        # available in Python 3.7+
        date = datetime.fromisoformat(date)

    os_name = f.get("os_name")
    assert isinstance(os_name, str)

    os_version = f.get("os_version")
    assert isinstance(os_version, str)

    is_64_bit = f.get("is_64_bit")
    assert isinstance(is_64_bit, bool)

    arch = f.get("architecture")
    assert isinstance(arch, str)

    vm = f.get("vm")
    assert isinstance(vm, str)

    pip_version = f.get("pip_version")
    assert isinstance(pip_version, str)

    version = f.get("version")
    assert isinstance(version, str)

    lang = f.get("lang")
    assert lang == "python"

    in_venv = f.get("in_virtual_env")
    assert in_venv is True

    lang_version = f.get("lang_version")
    if sys.version_info == (3, 7, 0):
        assert "3.7" in lang_version
    elif sys.version_info == (3, 6, 0):
        assert "3.6" in lang_version
    elif sys.version_info == (2, 7, 0):
        assert "2.7" in lang_version

    agent_url = f.get("agent_url")
    assert agent_url == "http://localhost:8126"

    assert "agent_error" in f
    agent_error = f.get("agent_error")
    assert agent_error is None

    assert f.get("env") == ""
    assert f.get("is_global_tracer") is True
    assert f.get("tracer_enabled") is True
    assert f.get("sampler_type") == "DatadogSampler"
    assert f.get("priority_sampler_type") == "RateByServiceSampler"
    assert f.get("service") == ""
    assert f.get("dd_version") == ""
    assert f.get("debug") is False
    assert f.get("enabled_cli") is False
    assert f.get("analytics_enabled") is False
    assert f.get("log_injection_enabled") is False
    assert f.get("health_metrics_enabled") is False
    assert f.get("runtime_metrics_enabled") is False
    assert f.get("priority_sampling_enabled") is True
    assert f.get("sampler_rules") == []
    assert f.get("global_tags") == ""
    assert f.get("tracer_tags") == ""

    icfg = f.get("integrations")
    assert icfg["django"] == "N/A"
    assert icfg["flask"] == "N/A"


def test_debug_post_configure():
    tracer = ddtrace.Tracer()
    tracer.configure(
        hostname="0.0.0.0",
        port=1234,
        priority_sampling=True,
    )

    f = debug.collect(tracer)

    agent_url = f.get("agent_url")
    assert agent_url == "http://0.0.0.0:1234"

    assert f.get("is_global_tracer") is False
    assert f.get("tracer_enabled") is True

    agent_error = f.get("agent_error")
    # Error code can differ between Python version
    assert re.match("^Agent not reachable.*Connection refused", agent_error)

    # Tracer doesn't support re-configure()-ing with a UDS after an initial
    # configure with normal http settings. So we need a new tracer instance.
    tracer = ddtrace.Tracer()
    tracer.configure(uds_path="/file.sock")

    f = debug.collect(tracer)

    agent_url = f.get("agent_url")
    assert agent_url == "unix:///file.sock"

    agent_error = f.get("agent_error")
    assert re.match("^Agent not reachable.*No such file or directory", agent_error)


class TestGlobalConfig(SubprocessTestCase):
    @run_in_subprocess(
        env_overrides=dict(
            DD_AGENT_HOST="0.0.0.0",
            DD_TRACE_AGENT_PORT="4321",
            DD_TRACE_ANALYTICS_ENABLED="true",
            DD_TRACE_HEALTH_METRICS_ENABLED="true",
            DD_LOGS_INJECTION="true",
            DD_ENV="prod",
            DD_VERSION="123456",
            DD_SERVICE="service",
            DD_TAGS="k1:v1,k2:v2",
        )
    )
    def test_env_config(self):
        f = debug.collect(ddtrace.tracer)
        assert f.get("agent_url") == "http://0.0.0.0:4321"
        assert f.get("analytics_enabled") is True
        assert f.get("health_metrics_enabled") is True
        assert f.get("log_injection_enabled") is True
        assert f.get("priority_sampling_enabled") is True
        assert f.get("env") == "prod"
        assert f.get("dd_version") == "123456"
        assert f.get("service") == "service"
        assert f.get("global_tags") == "k1:v1,k2:v2"
        assert f.get("tracer_tags") in ["k1:v1,k2:v2", "k2:v2,k1:v1"]
        assert f.get("tracer_enabled") is True

        icfg = f.get("integrations")
        assert icfg["django"] == "N/A"

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_AGENT_URL="http://0.0.0.0:1234",
        )
    )
    def test_trace_agent_url(self):
        f = debug.collect(ddtrace.tracer)
        assert f.get("agent_url") == "http://0.0.0.0:1234"

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_AGENT_URL="http://localhost:8126",
            DD_TRACE_STARTUP_LOGS="1",
        )
    )
    def test_tracer_loglevel_info_connection(self):
        tracer = ddtrace.Tracer()
        logging.basicConfig(level=logging.INFO)
        with mock.patch.object(logging.Logger, "log") as mock_logger:
            tracer.configure()
        assert mock.call(logging.INFO, re_matcher("- DATADOG TRACER CONFIGURATION - ")) in mock_logger.mock_calls

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_AGENT_URL="http://0.0.0.0:1234",
            DD_TRACE_STARTUP_LOGS="1",
        )
    )
    def test_tracer_loglevel_info_no_connection(self):
        tracer = ddtrace.Tracer()
        logging.basicConfig(level=logging.INFO)
        with mock.patch.object(logging.Logger, "log") as mock_logger:
            tracer.configure()
        # Python 2 logs will go to stderr directly since there's no log handler
        if PY3:
            assert mock.call(logging.INFO, re_matcher("- DATADOG TRACER CONFIGURATION - ")) in mock_logger.mock_calls
            assert mock.call(logging.WARNING, re_matcher("- DATADOG TRACER DIAGNOSTIC - ")) in mock_logger.mock_calls

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_AGENT_URL="http://0.0.0.0:1234",
        )
    )
    def test_tracer_loglevel_info_no_connection_py2_handler(self):
        tracer = ddtrace.Tracer()
        logging.basicConfig()
        with mock.patch.object(logging.Logger, "log") as mock_logger:
            tracer.configure()
            if PY2:
                assert mock_logger.mock_calls == []

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_AGENT_URL="http://0.0.0.0:1234",
            DD_TRACE_STARTUP_LOGS="0",
        )
    )
    def test_tracer_log_disabled_error(self):
        tracer = ddtrace.Tracer()
        with mock.patch.object(logging.Logger, "log") as mock_logger:
            tracer.configure()
        assert mock_logger.mock_calls == []

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_AGENT_URL="http://0.0.0.0:8126",
            DD_TRACE_STARTUP_LOGS="0",
        )
    )
    def test_tracer_log_disabled(self):
        tracer = ddtrace.Tracer()
        with mock.patch.object(logging.Logger, "log") as mock_logger:
            tracer.configure()
        assert mock_logger.mock_calls == []

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_AGENT_URL="http://0.0.0.0:8126",
        )
    )
    def test_tracer_info_level_log(self):
        logging.basicConfig(level=logging.INFO)
        tracer = ddtrace.Tracer()
        with mock.patch.object(logging.Logger, "log") as mock_logger:
            tracer.configure()
        assert mock_logger.mock_calls == []


def test_runtime_metrics_enabled_via_manual_start(ddtrace_run_python_code_in_subprocess):
    _, _, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import ddtrace
from ddtrace.internal import debug
from ddtrace.runtime import RuntimeMetrics

f = debug.collect(ddtrace.tracer)
assert f.get("runtime_metrics_enabled") is False

RuntimeMetrics.enable()
f = debug.collect(ddtrace.tracer)
assert f.get("runtime_metrics_enabled") is True

RuntimeMetrics.disable()
f = debug.collect(ddtrace.tracer)
assert f.get("runtime_metrics_enabled") is False
""",
    )
    assert status == 0


def test_runtime_metrics_enabled_via_env_var_start(monkeypatch, ddtrace_run_python_code_in_subprocess):
    # default, no env variable set
    _, _, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import ddtrace
from ddtrace.internal import debug
f = debug.collect(ddtrace.tracer)
assert f.get("runtime_metrics_enabled") is False
""",
    )
    assert status == 0

    # Explicitly set env variable
    monkeypatch.setenv("DD_RUNTIME_METRICS_ENABLED", "true")
    _, _, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import ddtrace
from ddtrace.internal import debug
f = debug.collect(ddtrace.tracer)
assert f.get("runtime_metrics_enabled") is True
""",
    )
    assert status == 0


def test_to_json():
    info = debug.collect(ddtrace.tracer)
    json.dumps(info)


def test_agentless(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "something")
    tracer = ddtrace.Tracer()
    info = debug.collect(tracer)

    assert info.get("agent_url") == "AGENTLESS"


def test_custom_writer():
    tracer = ddtrace.Tracer()

    class CustomWriter(TraceWriter):
        def recreate(self):
            # type: () -> TraceWriter
            return self

        def stop(self, timeout=None):
            # type: (Optional[float]) -> None
            pass

        def write(self, spans=None):
            # type: (Optional[List[Span]]) -> None
            pass

        def flush_queue(self):
            # type: () -> None
            pass

    tracer._writer = CustomWriter()
    info = debug.collect(tracer)

    assert info.get("agent_url") == "CUSTOM"


def test_different_samplers():
    tracer = ddtrace.Tracer()
    tracer.configure(sampler=ddtrace.sampler.RateSampler())
    info = debug.collect(tracer)

    assert info.get("sampler_type") == "RateSampler"


def test_startup_logs_sampling_rules():
    tracer = ddtrace.Tracer()
    sampler = ddtrace.sampler.DatadogSampler(rules=[ddtrace.sampler.SamplingRule(sample_rate=1.0)])
    tracer.configure(sampler=sampler)
    f = debug.collect(tracer)

    assert f.get("sampler_rules") == ["SamplingRule(sample_rate=1.0, service='NO_RULE', name='NO_RULE')"]

    sampler = ddtrace.sampler.DatadogSampler(
        rules=[ddtrace.sampler.SamplingRule(sample_rate=1.0, service="xyz", name="abc")]
    )
    tracer.configure(sampler=sampler)
    f = debug.collect(tracer)

    assert f.get("sampler_rules") == ["SamplingRule(sample_rate=1.0, service='xyz', name='abc')"]


def test_error_output_ddtracerun_debug_mode():
    p = subprocess.Popen(
        ["ddtrace-run", "python", "tests/integration/hello.py"],
        env=dict(
            DD_TRACE_AGENT_URL="http://localhost:8126", DD_TRACE_DEBUG="true", DD_CALL_BASIC_CONFIG="true", **os.environ
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert b"Test success" in p.stdout.read()
    assert b"DATADOG TRACER CONFIGURATION" in p.stderr.read()
    assert b"DATADOG TRACER DIAGNOSTIC - Agent not reachable" not in p.stderr.read()

    # No connection to agent, debug mode enabled
    p = subprocess.Popen(
        ["ddtrace-run", "python", "tests/integration/hello.py"],
        env=dict(
            DD_TRACE_AGENT_URL="http://localhost:4321", DD_TRACE_DEBUG="true", DD_CALL_BASIC_CONFIG="true", **os.environ
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert b"Test success" in p.stdout.read()
    stderr = p.stderr.read()
    assert b"DATADOG TRACER CONFIGURATION" in stderr
    assert b"DATADOG TRACER DIAGNOSTIC - Agent not reachable" in stderr


def test_error_output_ddtracerun():
    # Connection to agent, debug mode disabled
    p = subprocess.Popen(
        ["ddtrace-run", "python", "tests/integration/hello.py"],
        env=dict(DD_TRACE_AGENT_URL="http://localhost:8126", DD_TRACE_DEBUG="false", **os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert b"Test success" in p.stdout.read()
    stderr = p.stderr.read()
    assert b"DATADOG TRACER CONFIGURATION" not in stderr
    assert b"DATADOG TRACER DIAGNOSTIC - Agent not reachable" not in stderr

    # No connection to agent, debug mode disabled
    p = subprocess.Popen(
        ["ddtrace-run", "python", "tests/integration/hello.py"],
        env=dict(DD_TRACE_AGENT_URL="http://localhost:4321", DD_TRACE_DEBUG="false", **os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert b"Test success" in p.stdout.read()
    stderr = p.stderr.read()
    assert b"DATADOG TRACER CONFIGURATION" not in stderr
    assert b"DATADOG TRACER DIAGNOSTIC - Agent not reachable" not in stderr


def test_debug_span_log():
    p = subprocess.Popen(
        ["python", "-c", 'import os; print(os.environ);import ddtrace; ddtrace.tracer.trace("span").finish()'],
        env=dict(
            DD_TRACE_AGENT_URL="http://localhost:8126", DD_TRACE_DEBUG="true", DD_CALL_BASIC_CONFIG="true", **os.environ
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    stderr = p.stderr.read()
    assert b"finishing span name='span'" in stderr


def test_partial_flush_log():
    tracer = ddtrace.Tracer()

    tracer.configure(
        partial_flush_enabled=True,
        partial_flush_min_spans=300,
    )

    f = debug.collect(tracer)

    partial_flush_enabled = f.get("partial_flush_enabled")
    partial_flush_min_spans = f.get("partial_flush_min_spans")

    assert partial_flush_enabled is True
    assert partial_flush_min_spans == 300


@pytest.mark.subprocess(
    env=dict(
        DD_TRACE_PARTIAL_FLUSH_ENABLED="true",
        DD_TRACE_PARTIAL_FLUSH_MIN_SPANS="2",
    )
)
def test_partial_flush_log_subprocess():
    from ddtrace import tracer

    assert tracer._partial_flush_enabled is True
    assert tracer._partial_flush_min_spans == 2
