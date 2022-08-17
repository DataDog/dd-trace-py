import pytest

from ddtrace.internal import agent
from ddtrace.settings import Config


def test_trace_hostname(monkeypatch):
    assert Config().agent.hostname == "localhost"
    monkeypatch.setenv("DD_AGENT_HOST", "host")
    assert Config().agent.hostname == "host"


def test_stats_hostname(monkeypatch):
    assert Config().agent.hostname == "localhost"
    monkeypatch.setenv("DD_AGENT_HOST", "host")
    assert Config().agent.hostname == "host"


def test_trace_port(monkeypatch):
    assert Config().agent.port == 8126
    monkeypatch.setenv("DD_AGENT_PORT", "1234")
    assert Config().agent.port == 1234


def test_stats_port(monkeypatch):
    assert Config().agent.stats_port == 8125
    monkeypatch.setenv("DD_DOGSTATSD_PORT", "1235")
    assert Config().agent.stats_port == 1235


def test_trace_url(monkeypatch, mocker):
    mock_exists = mocker.patch("os.path.exists")

    # with nothing set by user, and the default UDS available, we choose UDS
    mock_exists.return_value = True
    assert Config().trace_agent_url == "unix:///var/run/datadog/apm.socket"
    mock_exists.reset_mock()

    # with nothing set by user, and the default UDS unavailable, we choose default http address
    mock_exists.return_value = False
    assert Config().trace_agent_url == "http://localhost:8126"
    mock_exists.reset_mock()

    # with host set by user, and default UDS unavailable, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_AGENT_HOST", "mars")
        assert Config().trace_agent_url == "http://mars:8126"

    # with host set by user, and default UDS available, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_AGENT_HOST", "mars")
        assert Config().trace_agent_url == "http://mars:8126"


def test_stats_url(monkeypatch, mocker):
    mock_exists = mocker.patch("os.path.exists")

    # with nothing set by user, and the default UDS available, we choose UDS
    mock_exists.return_value = True
    assert Config().agent.stats_url == "unix:///var/run/datadog/dsd.socket"
    mock_exists.reset_mock()

    # with nothing set by user, and the default UDS unavailable, we choose default UDP address
    mock_exists.return_value = False
    assert Config().agent.stats_url == "udp://localhost:8125"
    mock_exists.reset_mock()

    # with port set by user, and default UDS unavailable, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_DOGSTATSD_PORT", "1235")
        assert Config().agent.stats_url == "udp://localhost:1235"

    # with host set by user, and default UDS unavailable, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_AGENT_HOST", "mars")
        assert Config().agent.stats_url == "udp://mars:8125"

    # with host and port set by user, and default UDS unavailable, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_DOGSTATSD_PORT", "1235")
        m.setenv("DD_AGENT_HOST", "mars")
        assert Config().agent.stats_url == "udp://mars:1235"

    # with port set by user, and default UDS available, we choose user settings
    mock_exists.return_value = True

    with monkeypatch.context() as m:
        m.setenv("DD_DOGSTATSD_PORT", "1235")
        assert Config().agent.stats_url == "udp://localhost:1235"

    # with host set by user, and default UDS available, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_AGENT_HOST", "mars")
        assert Config().agent.stats_url == "udp://mars:8125"

    # with host and port set by user, and default UDS available, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_DOGSTATSD_PORT", "1235")
        m.setenv("DD_AGENT_HOST", "mars")
        assert Config().agent.stats_url == "udp://mars:1235"

    # with port, host, and url set by user, and default UDS available, we choose url
    with monkeypatch.context() as m:
        m.setenv("DD_DOGSTATSD_PORT", "1235")
        m.setenv("DD_AGENT_HOST", "mars")
        m.setenv("DD_DOGSTATSD_URL", "udp://saturn:1111")
        assert Config().agent.stats_url == "udp://saturn:1111"

    # with port, host, and url set by user, and default UDS unavailable, we choose url
    mock_exists.return_value = False

    with monkeypatch.context() as m:
        m.setenv("DD_DOGSTATSD_PORT", "1235")
        m.setenv("DD_AGENT_HOST", "mars")
        m.setenv("DD_DOGSTATSD_URL", "udp://saturn:1111")
        assert Config().agent.stats_url == "udp://saturn:1111"


def test_get_connection():
    with pytest.raises(ValueError):
        agent.get_connection("bad://localhost:1234", timeout=1)

    with pytest.raises(ValueError):
        agent.get_connection(":#@$@!//localhost:1234", timeout=1)

    with pytest.raises(ValueError):
        agent.get_connection("", timeout=1)


def test_verify_url():
    agent.verify_url("http://localhost:1234")
    agent.verify_url("https://localhost:1234")
    agent.verify_url("https://localhost")
    agent.verify_url("http://192.168.1.1")
    agent.verify_url("unix:///file.sock")
    agent.verify_url("unix:///file")

    with pytest.raises(ValueError) as e:
        agent.verify_url("unix://")
    assert str(e.value) == "Invalid file path in Agent URL 'unix://'"

    with pytest.raises(ValueError) as e:
        agent.verify_url("http2://localhost:1234")
    assert (
        str(e.value)
        == "Unsupported protocol 'http2' in Agent URL 'http2://localhost:1234'. Must be one of: http, https, unix"
    )

    with pytest.raises(ValueError) as e:
        agent.verify_url("adsf")
    assert str(e.value) == "Unsupported protocol '' in Agent URL 'adsf'. Must be one of: http, https, unix"

    with pytest.raises(ValueError) as e:
        agent.verify_url("http://")
    assert str(e.value) == "Invalid hostname in Agent URL 'http://'"

    with pytest.raises(ValueError) as e:
        agent.verify_url("https://")
    assert str(e.value) == "Invalid hostname in Agent URL 'https://'"

    with pytest.raises(ValueError) as e:
        agent.verify_url("unix://")
    assert str(e.value) == "Invalid file path in Agent URL 'unix://'"
