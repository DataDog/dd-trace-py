from ddtrace.internal.logger import log_filter
from ddtrace.vendor.dogstatsd.base import DogStatsd, log, DEFAULT_HOST, DEFAULT_PORT


def test_dogstatsd_logger():
    """Ensure dogstatsd logger is initialized as a rate limited logger"""
    assert log_filter in log.filters

def test_dogstatsd_recpects_host_port_env_vars(monkeypatch):
    """Ensure dogstatsd base respects DD_AGENT_HOST and DD_DOGSTATSD_PORT env vars"""
    monkeypatch.setenv("DD_AGENT_HOST", "env-host")
    monkeypatch.setenv("DD_DOGSTATSD_PORT", "1234")

    client = DogStatsd()
    assert client.host == "env-host"
    assert client.port == 1234


def test_dogstatsd_recpects_constructor_host_port(monkeypatch):
    """Ensure dogstatsd base respects constructor host and port over env vars"""

    monkeypatch.setenv("DD_AGENT_HOST", "env-host")
    monkeypatch.setenv("DD_DOGSTATSD_PORT", "1234")

    client = DogStatsd(host="localhost", port=8125)
    assert client.host == "localhost"
    assert client.port == 8125


def test_dogstatsd_uses_default_host_port(monkeypatch):
    """Ensure dogstatsd base uses default host and port when no env vars are set and
    no constructor values are provided
    """
    
    monkeypatch.delenv("DD_AGENT_HOST", raising=False)
    monkeypatch.delenv("DD_DOGSTATSD_PORT", raising=False)

    client = DogStatsd()
    assert client.host == DEFAULT_HOST
    assert client.port == DEFAULT_PORT
