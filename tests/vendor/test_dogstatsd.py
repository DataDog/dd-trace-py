from pytest import monkeypatch
from ddtrace.internal.logger import log_filter
from ddtrace.vendor.dogstatsd.base import DogStatsd, log, DEFAULT_HOST, DEFAULT_PORT


def test_dogstatsd_logger():
    """Ensure dogstatsd logger is initialized as a rate limited logger"""
    assert log_filter in log.filters

@monkeypatch.setenv("DD_AGENT_HOST", "env-host")
@monkeypatch.setenv("DD_DOGSTATSD_PORT", "1234")
def test_dogstatsd_recpects_host_port_env_vars():
    """Ensure dogstatsd base respects DD_AGENT_HOST and DD_DOGSTATSD_PORT env vars"""

    client = DogStatsd()
    assert client.host == "env-host"
    assert client.port == 1234


@monkeypatch.setenv("DD_AGENT_HOST", "env-host")
@monkeypatch.setenv("DD_DOGSTATSD_PORT", "1234")
def test_dogstatsd_recpects_constructor_host_port():
    """Ensure dogstatsd base respects constructor host and port over env vars"""

    client = DogStatsd(host="test-host", port=4321)
    assert client.host == "test-host"
    assert client.port == 4321


@monkeypatch.delenv("DD_AGENT_HOST")
@monkeypatch.delenv("DD_DOGSTATSD_PORT")
def test_dogstatsd_uses_default_host_port():
    """Ensure dogstatsd base uses default host and port when no env vars are set and
    no constructor values are provided
    """

    client = DogStatsd()
    assert client.host == DEFAULT_HOST
    assert client.port == DEFAULT_PORT
