import json
import logging
import os
import re
import subprocess

import mock
import pytest

import ddtrace
import ddtrace._trace.sampler
from ddtrace.internal import debug
from ddtrace.internal.writer import AgentWriter
from tests.integration.utils import AGENT_VERSION
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess


pytestmark = pytest.mark.skipif(AGENT_VERSION == "testagent", reason="The test agent doesn't support startup logs.")


def re_matcher(pattern):
    pattern = re.compile(pattern)

    class Match:
        def __eq__(self, other):
            return pattern.match(other)

    return Match()


@pytest.mark.subprocess()
def test_standard_tags():
    from datetime import datetime

    import ddtrace
    from ddtrace.internal import debug

    f = debug.collect(ddtrace.tracer)

    date = f.get("date")
    assert isinstance(date, str)

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

    agent_url = f.get("agent_url")
    assert agent_url == "http://localhost:8126"

    assert "agent_error" in f
    agent_error = f.get("agent_error")
    assert agent_error is None

    assert f.get("env") == ""
    assert f.get("is_global_tracer") is True
    assert f.get("tracer_enabled") is True
    assert f.get("sampler_type") == "DatadogSampler"
    assert f.get("priority_sampler_type") == "N/A"
    assert f.get("service") == "ddtrace_subprocess_dir"
    assert f.get("dd_version") == ""
    assert f.get("debug") is False
    assert f.get("enabled_cli") is False
    assert f.get("log_injection_enabled") is False
    assert f.get("health_metrics_enabled") is False
    assert f.get("runtime_metrics_enabled") is False
    assert f.get("sampler_rules") == []
    assert f.get("global_tags") == ""
    assert f.get("tracer_tags") == ""

    icfg = f.get("integrations")
    assert icfg["django"] == "N/A"
    assert icfg["flask"] == "N/A"


@pytest.mark.subprocess(env={"DD_TRACE_AGENT_URL": "unix:///file.sock"})
def test_debug_post_configure_uds():
    import re

    from ddtrace.internal import debug
    from ddtrace.trace import tracer

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
        assert f.get("health_metrics_enabled") is True
        assert f.get("log_injection_enabled") is True
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
        logging.basicConfig(level=logging.INFO)
        with mock.patch.object(logging.Logger, "log") as mock_logger:
            # shove an unserializable object into the config log output
            # regression: this used to cause an exception to be raised
            ddtrace.config.version = AgentWriter(agent_url="foobar")
            ddtrace.trace.tracer.configure()
        assert mock.call(logging.INFO, re_matcher("- DATADOG TRACER CONFIGURATION - ")) in mock_logger.mock_calls

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_AGENT_URL="http://0.0.0.0:1234",
            DD_TRACE_STARTUP_LOGS="1",
        )
    )
    def test_tracer_loglevel_info_no_connection(self):
        logging.basicConfig(level=logging.INFO)
        with mock.patch.object(logging.Logger, "log") as mock_logger:
            ddtrace.trace.tracer.configure()
        assert mock.call(logging.INFO, re_matcher("- DATADOG TRACER CONFIGURATION - ")) in mock_logger.mock_calls
        assert mock.call(logging.WARNING, re_matcher("- DATADOG TRACER DIAGNOSTIC - ")) in mock_logger.mock_calls

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_AGENT_URL="http://0.0.0.0:1234",
            DD_TRACE_STARTUP_LOGS="0",
        )
    )
    def test_tracer_log_disabled_error(self):
        with mock.patch.object(logging.Logger, "log") as mock_logger:
            ddtrace.trace.tracer.configure()
        assert mock_logger.mock_calls == []

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_AGENT_URL="http://0.0.0.0:8126",
            DD_TRACE_STARTUP_LOGS="0",
        )
    )
    def test_tracer_log_disabled(self):
        with mock.patch.object(logging.Logger, "log") as mock_logger:
            ddtrace.trace.tracer.configure()
        assert mock_logger.mock_calls == []

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_AGENT_URL="http://0.0.0.0:8126",
        )
    )
    def test_tracer_info_level_log(self):
        logging.basicConfig(level=logging.INFO)
        with mock.patch.object(logging.Logger, "log") as mock_logger:
            ddtrace.trace.tracer.configure()
        assert mock_logger.mock_calls == []


@pytest.mark.subprocess(ddtrace_run=True, err=None)
def test_runtime_metrics_enabled_via_manual_start():
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


@pytest.mark.subprocess(ddtrace_run=True, parametrize={"DD_RUNTIME_METRICS_ENABLED": ["0", "true"]}, err=None)
def test_runtime_metrics_enabled_via_env_var_start():
    import os

    import ddtrace
    from ddtrace.internal import debug
    from ddtrace.internal.utils.formats import asbool

    f = debug.collect(ddtrace.tracer)
    assert f.get("runtime_metrics_enabled") is asbool(os.getenv("DD_RUNTIME_METRICS_ENABLED")), (
        f.get("runtime_metrics_enabled"),
        asbool(os.getenv("DD_RUNTIME_METRICS_ENABLED")),
    )


def test_to_json():
    info = debug.collect(ddtrace.tracer)
    json.dumps(info)


@pytest.mark.subprocess(env={"AWS_LAMBDA_FUNCTION_NAME": "something"})
def test_agentless(monkeypatch):
    from ddtrace.internal import debug
    from ddtrace.trace import tracer

    info = debug.collect(tracer)
    assert info.get("agent_url") == "AGENTLESS"


@pytest.mark.subprocess()
def test_custom_writer():
    from typing import List
    from typing import Optional

    from ddtrace.internal import debug
    from ddtrace.internal.writer import TraceWriter
    from ddtrace.trace import Span
    from ddtrace.trace import tracer

    class CustomWriter(TraceWriter):
        def recreate(self) -> TraceWriter:
            return self

        def stop(self, timeout: Optional[float] = None) -> None:
            pass

        def write(self, spans: Optional[List[Span]] = None) -> None:
            pass

        def flush_queue(self) -> None:
            pass

    tracer._span_aggregator.writer = CustomWriter()
    info = debug.collect(tracer)

    assert info.get("agent_url") == "CUSTOM"


@pytest.mark.subprocess(env={"DD_TRACE_SAMPLING_RULES": '[{"sample_rate":1.0}]'})
def test_startup_logs_sampling_rules():
    from ddtrace.internal import debug
    from ddtrace.trace import tracer

    f = debug.collect(tracer)

    assert f.get("sampler_rules") == [
        "SamplingRule(sample_rate=1.0, service='NO_RULE', name='NO_RULE', resource='NO_RULE',"
        " tags='NO_RULE', provenance='default')"
    ]


def test_error_output_ddtracerun_debug_mode():
    p = subprocess.Popen(
        ["ddtrace-run", "python", "tests/integration/hello.py"],
        env=dict(DD_TRACE_AGENT_URL="http://localhost:8126", DD_TRACE_DEBUG="true", **os.environ),
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
        env=dict(DD_TRACE_AGENT_URL="http://localhost:4321", DD_TRACE_DEBUG="true", **os.environ),
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
        env=dict(DD_TRACE_AGENT_URL="http://localhost:8126", DD_TRACE_DEBUG="true", **os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    stderr = p.stderr.read()
    assert b"finishing span name='span'" in stderr


@pytest.mark.subprocess(
    env=dict(
        DD_TRACE_PARTIAL_FLUSH_ENABLED="true",
        DD_TRACE_PARTIAL_FLUSH_MIN_SPANS="300",
    )
)
def test_partial_flush_log():
    from ddtrace import tracer
    from ddtrace.internal import debug

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
    from ddtrace.trace import tracer

    assert tracer._span_aggregator.partial_flush_enabled is True
    assert tracer._span_aggregator.partial_flush_min_spans == 2
