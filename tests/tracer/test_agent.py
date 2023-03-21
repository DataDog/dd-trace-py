import mock
import pytest

from ddtrace.internal import agent
from ddtrace.internal.agent import info


@pytest.mark.parametrize(
    "hostname,expected",
    [
        (None, False),
        ("10.0.0.1", False),
        ("192.168.1.1", False),
        ("https://www.datadog.com", False),
        ("2001:db8:3333:4444:5555:6666:7777:8888", True),
        ("2001:db8:3333:4444:CCCC:DDDD:EEEE:FFFF", True),
        ("[2001:db8:3333:4444:5555:6666:7777:8888]", False),
        ("::", True),
    ],
)
def test_is_ipv6_hostname(hostname, expected):
    assert agent.is_ipv6_hostname(hostname) == expected


def test_trace_hostname(monkeypatch):
    assert agent.get_trace_hostname() == "localhost"
    monkeypatch.setenv("DD_AGENT_HOST", "host")
    assert agent.get_trace_hostname() == "host"
    monkeypatch.setenv("DD_AGENT_HOST", "2001:db8:3333:4444:CCCC:DDDD:EEEE:FFFF")
    assert agent.get_trace_hostname() == "[2001:db8:3333:4444:CCCC:DDDD:EEEE:FFFF]"


def test_stats_hostname(monkeypatch):
    assert agent.get_stats_hostname() == "localhost"
    monkeypatch.setenv("DD_AGENT_HOST", "host")
    assert agent.get_stats_hostname() == "host"
    monkeypatch.setenv("DD_AGENT_HOST", "2001:db8:3333:4444:CCCC:DDDD:EEEE:FFFF")
    assert agent.get_stats_hostname() == "[2001:db8:3333:4444:CCCC:DDDD:EEEE:FFFF]"


def test_trace_port(monkeypatch):
    assert agent.get_trace_port() == 8126
    monkeypatch.setenv("DD_TRACE_AGENT_PORT", "1235")
    assert agent.get_trace_port() == 1235
    monkeypatch.setenv("DD_AGENT_PORT", "1234")
    assert agent.get_trace_port() == 1234


def test_stats_port(monkeypatch):
    assert agent.get_stats_port() == 8125
    monkeypatch.setenv("DD_DOGSTATSD_PORT", "1235")
    assert agent.get_stats_port() == 1235


def test_trace_url(monkeypatch, mocker):
    mock_exists = mocker.patch("os.path.exists")

    # with nothing set by user, and the default UDS available, we choose UDS
    mock_exists.return_value = True
    assert agent.get_trace_url() == "unix:///var/run/datadog/apm.socket"
    mock_exists.assert_called_once_with("/var/run/datadog/apm.socket")
    mock_exists.reset_mock()

    # with nothing set by user, and the default UDS unavailable, we choose default http address
    mock_exists.return_value = False
    assert agent.get_trace_url() == "http://localhost:8126"
    mock_exists.assert_called_once_with("/var/run/datadog/apm.socket")
    mock_exists.reset_mock()

    # with port set by user, and default UDS unavailable, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_TRACE_AGENT_PORT", "1235")
        assert agent.get_trace_url() == "http://localhost:1235"
        mock_exists.assert_not_called()

    # with host set by user, and default UDS unavailable, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_AGENT_HOST", "mars")
        assert agent.get_trace_url() == "http://mars:8126"
        mock_exists.assert_not_called()

    # with host and port set by user, and default UDS unavailable, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_TRACE_AGENT_PORT", "1235")
        m.setenv("DD_AGENT_HOST", "mars")
        assert agent.get_trace_url() == "http://mars:1235"
        mock_exists.assert_not_called()

    # with port set by user, and default UDS available, we choose user settings
    mock_exists.return_value = True

    with monkeypatch.context() as m:
        m.setenv("DD_TRACE_AGENT_PORT", "1235")
        assert agent.get_trace_url() == "http://localhost:1235"
        mock_exists.assert_not_called()

    # with host set by user, and default UDS available, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_AGENT_HOST", "mars")
        assert agent.get_trace_url() == "http://mars:8126"
        mock_exists.assert_not_called()

    # with host and port set by user, and default UDS available, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_TRACE_AGENT_PORT", "1235")
        m.setenv("DD_AGENT_HOST", "mars")
        assert agent.get_trace_url() == "http://mars:1235"
        mock_exists.assert_not_called()

    # with port, host, and url set by user, and default UDS available, we choose url
    with monkeypatch.context() as m:
        m.setenv("DD_TRACE_AGENT_PORT", "1235")
        m.setenv("DD_AGENT_HOST", "mars")
        m.setenv("DD_TRACE_AGENT_URL", "http://saturn:1111")
        assert agent.get_trace_url() == "http://saturn:1111"
        mock_exists.assert_not_called()

    # with port, host, and url set by user, and default UDS unavailable, we choose url
    mock_exists.return_value = False

    with monkeypatch.context() as m:
        m.setenv("DD_TRACE_AGENT_PORT", "1235")
        m.setenv("DD_AGENT_HOST", "mars")
        m.setenv("DD_TRACE_AGENT_URL", "http://saturn:1111")
        assert agent.get_trace_url() == "http://saturn:1111"
        mock_exists.assert_not_called()


def test_stats_url(monkeypatch, mocker):
    mock_exists = mocker.patch("os.path.exists")

    # with nothing set by user, and the default UDS available, we choose UDS
    mock_exists.return_value = True
    assert agent.get_stats_url() == "unix:///var/run/datadog/dsd.socket"
    mock_exists.assert_called_once_with("/var/run/datadog/dsd.socket")
    mock_exists.reset_mock()

    # with nothing set by user, and the default UDS unavailable, we choose default UDP address
    mock_exists.return_value = False
    assert agent.get_stats_url() == "udp://localhost:8125"
    mock_exists.assert_called_once_with("/var/run/datadog/dsd.socket")
    mock_exists.reset_mock()

    # with port set by user, and default UDS unavailable, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_DOGSTATSD_PORT", "1235")
        assert agent.get_stats_url() == "udp://localhost:1235"
        mock_exists.assert_not_called()

    # with host set by user, and default UDS unavailable, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_AGENT_HOST", "mars")
        assert agent.get_stats_url() == "udp://mars:8125"
        mock_exists.assert_not_called()

    # with host and port set by user, and default UDS unavailable, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_DOGSTATSD_PORT", "1235")
        m.setenv("DD_AGENT_HOST", "mars")
        assert agent.get_stats_url() == "udp://mars:1235"
        mock_exists.assert_not_called()

    # with port set by user, and default UDS available, we choose user settings
    mock_exists.return_value = True

    with monkeypatch.context() as m:
        m.setenv("DD_DOGSTATSD_PORT", "1235")
        assert agent.get_stats_url() == "udp://localhost:1235"
        mock_exists.assert_not_called()

    # with host set by user, and default UDS available, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_AGENT_HOST", "mars")
        assert agent.get_stats_url() == "udp://mars:8125"
        mock_exists.assert_not_called()

    # with host and port set by user, and default UDS available, we choose user settings
    with monkeypatch.context() as m:
        m.setenv("DD_DOGSTATSD_PORT", "1235")
        m.setenv("DD_AGENT_HOST", "mars")
        assert agent.get_stats_url() == "udp://mars:1235"
        mock_exists.assert_not_called()

    # with port, host, and url set by user, and default UDS available, we choose url
    with monkeypatch.context() as m:
        m.setenv("DD_DOGSTATSD_PORT", "1235")
        m.setenv("DD_AGENT_HOST", "mars")
        m.setenv("DD_DOGSTATSD_URL", "udp://saturn:1111")
        assert agent.get_stats_url() == "udp://saturn:1111"
        mock_exists.assert_not_called()

    # with port, host, and url set by user, and default UDS unavailable, we choose url
    mock_exists.return_value = False

    with monkeypatch.context() as m:
        m.setenv("DD_DOGSTATSD_PORT", "1235")
        m.setenv("DD_AGENT_HOST", "mars")
        m.setenv("DD_DOGSTATSD_URL", "udp://saturn:1111")
        assert agent.get_stats_url() == "udp://saturn:1111"
        mock_exists.assert_not_called()


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
    agent.verify_url("http://[2001:db8:3333:4444:CCCC:DDDD:EEEE:FFFF]")
    agent.verify_url("http://[2001:db8:3333:4444:CCCC:DDDD:EEEE:FFFF]:1234")
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


def _mock_raise(ex):
    raise ex


@pytest.mark.parametrize(
    "request_response,read_response, status_response, expected",
    [
        (None, "{}", 201, {}),
        (None, '{"result": "ok"}', 200, {"result": "ok"}),
        (None, "{}", 300, None),
        (None, "{}", 401, None),
        (None, "{}", 404, None),
        (None, "{}", 500, None),
    ],
)
@mock.patch("ddtrace.internal.agent.get_connection")
def test_info(mock_connection, request_response, read_response, status_response, expected):
    class MockResponse(object):
        def read(self):
            return read_response

        @property
        def status(self):
            return status_response

        @property
        def reason(self):
            return ""

    class MockConn:
        def request(self, *args, **kwargs):
            return request_response

        def getresponse(self):
            return MockResponse()

        def close(self):
            return None

    mock_connection.return_value = MockConn()
    assert info() == expected
