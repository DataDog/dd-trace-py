from datetime import datetime
import sys

import ddtrace
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
    assert f.get("profiling_enabled") is True

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
    assert agent_error == "Exception raised: [Errno 61] Connection refused"

    # DEV: Tracer doesn't support re-configure()-ing with a UDS after an
    # initial configure with normal http settings.
    # tracer.configure(uds_path="/file.sock"))

    tracer = ddtrace.Tracer()
    tracer.configure(uds_path="/file.sock")

    f = debug.collect(tracer)

    agent_url = f.get("agent_url")
    assert agent_url == "uds:///file.sock"

    agent_error = f.get("agent_error")
    assert agent_error == "Exception raised: [Errno 2] No such file or directory"


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
        assert f.get("tracer_enabled") == True

        icfg = f.get("integrations")
        assert icfg["django"] == "N/A"
