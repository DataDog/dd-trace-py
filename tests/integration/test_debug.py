from datetime import datetime
import json
import logging
import mock
import os
import re
import subprocess
import sys

import ddtrace
import ddtrace.sampler
from ddtrace.internal import debug

from tests.subprocesstest import SubprocessTestCase, run_in_subprocess


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
    assert f.get("priority_sampling_enabled") is True
    assert f.get("global_tags") == ""
    assert f.get("tracer_tags") == ""

    icfg = f.get("integrations")
    assert icfg["django"] == "N/A"
    assert icfg["flask"] == "N/A"


def test_debug_post_configure():
    tracer = ddtrace.Tracer()
    tracer.configure(
        hostname="0.0.0.0", port=1234, priority_sampling=True,
    )

    f = debug.collect(tracer)

    agent_url = f.get("agent_url")
    assert agent_url == "http://0.0.0.0:1234"

    assert f.get("is_global_tracer") is False
    assert f.get("tracer_enabled") is True

    agent_error = f.get("agent_error")
    # Error code can differ between Python version
    assert re.match("^Exception raised.*Connection refused", agent_error)

    # DEV: Tracer doesn't support re-configure()-ing with a UDS after an
    # initial configure with normal http settings.
    # tracer.configure(uds_path="/file.sock"))

    tracer = ddtrace.Tracer()
    tracer.configure(uds_path="/file.sock")

    f = debug.collect(tracer)

    agent_url = f.get("agent_url")
    assert agent_url == "uds:///file.sock"

    agent_error = f.get("agent_error")
    assert re.match("^Exception raised.*No such file or directory", agent_error)


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
        assert f.get("tracer_tags") == "k1:v1,k2:v2"
        assert f.get("tracer_enabled") is True

        icfg = f.get("integrations")
        assert icfg["django"] == "N/A"

    @run_in_subprocess(env_overrides=dict(DD_TRACE_AGENT_URL="http://0.0.0.0:1234",))
    def test_trace_agent_url(self):
        f = debug.collect(ddtrace.tracer)
        assert f.get("agent_url") == "http://0.0.0.0:1234"

    @run_in_subprocess(env_overrides=dict(DD_TRACE_AGENT_URL="http://localhost:8126",))
    def test_tracer_loglevel_info_connection(self):
        tracer = ddtrace.Tracer()
        tracer.log = mock.MagicMock()
        tracer.configure()
        assert tracer.log.info.call_count == 1
        assert tracer.log.error.call_count == 0

    @run_in_subprocess(env_overrides=dict(DD_TRACE_AGENT_URL="http://0.0.0.0:1234",))
    def test_tracer_loglevel_info_no_connection(self):
        tracer = ddtrace.Tracer()
        tracer.log = mock.MagicMock()
        tracer.configure()
        assert tracer.log.info.call_count == 1
        assert tracer.log.error.call_count == 1

    @run_in_subprocess(env_overrides=dict(DD_TRACE_AGENT_URL="http://0.0.0.0:1234", DD_TRACE_STARTUP_LOGS="0",))
    def test_tracer_log_disabled_error(self):
        tracer = ddtrace.Tracer()
        tracer.log = mock.MagicMock()
        tracer.configure()
        assert tracer.log.info.call_count == 0
        assert tracer.log.error.call_count == 1

    @run_in_subprocess(env_overrides=dict(DD_TRACE_AGENT_URL="http://0.0.0.0:8126", DD_TRACE_STARTUP_LOGS="0",))
    def test_tracer_log_disabled(self):
        tracer = ddtrace.Tracer()
        tracer.log = mock.MagicMock()
        tracer.configure()
        assert tracer.log.info.call_count == 0
        assert tracer.log.error.call_count == 0

    @run_in_subprocess(env_overrides=dict(DD_TRACE_AGENT_URL="http://0.0.0.0:8126",))
    def test_tracer_info_level_log(self):
        logging.basicConfig(level=logging.INFO)
        tracer = ddtrace.Tracer()
        tracer.log = mock.MagicMock()
        tracer.configure()
        assert tracer.log.info.call_count == 1
        assert tracer.log.error.call_count == 0


def test_to_json():
    info = debug.collect(ddtrace.tracer)
    js = debug.to_json(info)

    assert json.loads(js) == info


def test_agentless(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "something")
    tracer = ddtrace.Tracer()
    info = debug.collect(tracer)

    assert info.get("agent_url", "AGENTLESS")


def test_different_samplers():
    tracer = ddtrace.Tracer()
    tracer.configure(sampler=ddtrace.sampler.RateSampler())
    info = debug.collect(tracer)

    assert info.get("sampler_type") == "RateSampler"


def test_error_output_ddtracerun_debug_mode():
    r = subprocess.run(
        ["ddtrace-run", "python", "tests/integration/hello.py"],
        env=dict(DD_TRACE_AGENT_URL="http://localhost:8126", DATADOG_TRACE_DEBUG="true", **os.environ),
        capture_output=True,
    )
    assert b"Test success" in r.stdout
    assert b"DATADOG TRACER CONFIGURATION" in r.stderr
    assert b"DATADOG TRACER DIAGNOSTIC" not in r.stderr

    # No connection to agent, debug mode disabled
    r = subprocess.run(
        ["ddtrace-run", "python", "tests/integration/hello.py"],
        env=dict(DD_TRACE_AGENT_URL="http://localhost:4321", DATADOG_TRACE_DEBUG="true", **os.environ),
        capture_output=True,
    )
    assert b"Test success" in r.stdout
    assert b"DATADOG TRACER CONFIGURATION" in r.stderr
    assert b"DATADOG TRACER DIAGNOSTIC" in r.stderr


def test_error_output_ddtracerun():
    # Connection to agent, debug mode enabled
    r = subprocess.run(
        ["ddtrace-run", "python", "tests/integration/hello.py"],
        env=dict(DD_TRACE_AGENT_URL="http://localhost:8126", DATADOG_TRACE_DEBUG="false", **os.environ),
        capture_output=True,
    )
    assert b"Test success" in r.stdout
    assert b"DATADOG TRACER CONFIGURATION" not in r.stderr
    assert b"DATADOG TRACER DIAGNOSTIC" not in r.stderr

    # No connection to agent, debug mode enabled
    r = subprocess.run(
        ["ddtrace-run", "python", "tests/integration/hello.py"],
        env=dict(DD_TRACE_AGENT_URL="http://localhost:4321", DATADOG_TRACE_DEBUG="false", **os.environ),
        capture_output=True,
    )
    assert b"Test success" in r.stdout
    assert b"DATADOG TRACER CONFIGURATION" not in r.stderr
    assert b"DATADOG TRACER DIAGNOSTIC" in r.stderr
