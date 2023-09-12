import mock
import pytest

from ddtrace.internal import agent
from ddtrace.internal.agent import info
from ddtrace.internal.utils.http import verify_url


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


@pytest.mark.subprocess(parametrize={"DD_AGENT_HOST": ["host", "2001:db8:3333:4444:CCCC:DDDD:EEEE:FFFF"]})
def test_hostname():
    import os

    from ddtrace import config

    assert config._trace_agent_hostname == os.environ["DD_AGENT_HOST"]
    assert config._stats_agent_hostname == os.environ.get("DD_AGENT_HOST")


@pytest.mark.subprocess(env={"DD_AGENT_HOST": None})
def test_hostname_not_set():
    from ddtrace import config

    assert config._stats_agent_hostname is None
    assert config._trace_agent_hostname is None


@pytest.mark.subprocess(env={"DD_AGENT_PORT": "1235", "DD_TRACE_AGENT_PORT": "9999"})
def test_trace_port():
    from ddtrace import config

    assert config._trace_agent_port == "1235"


@pytest.mark.subprocess(env={"DD_TRACE_AGENT_PORT": "9999"})
def test_trace_port_legacy():
    from ddtrace import config

    assert config._trace_agent_port == "9999"


@pytest.mark.subprocess(env={"DD_AGENT_PORT": None, "DD_TRACE_AGENT_PORT": None})
def test_trace_port_not_set():
    from ddtrace import config

    assert config._trace_agent_port is None


@pytest.mark.subprocess(env={"DD_DOGSTATSD_PORT": "1235"})
def test_stats_port():
    from ddtrace import config

    assert config._stats_agent_port == "1235"


@pytest.mark.subprocess(env={"DD_DOGSTATSD_PORT": None})
def test_stats_port_not_set():
    from ddtrace import config

    assert config._stats_agent_port is None


@pytest.mark.subprocess(env={"DD_TRACE_AGENT_URL": None})
def test_trace_url_uds():
    # with nothing set by user, and the default UDS available, we choose UDS
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=True):
        assert agent.get_trace_url() == "unix:///var/run/datadog/apm.socket"


@pytest.mark.subprocess(env={"DD_TRACE_AGENT_URL": None})
def test_trace_url_default():
    # with nothing set by user, and the default UDS unavailable, we choose default http address
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=False):
        assert agent.get_trace_url() == "http://localhost:8126"


@pytest.mark.subprocess(env={"DD_TRACE_AGENT_PORT": "1235", "DD_TRACE_AGENT_URL": None})
def test_trace_url_with_port():
    # with port set by user, and default UDS unavailable, we choose user settings
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=False):
        assert agent.get_trace_url() == "http://localhost:1235"


@pytest.mark.subprocess(env={"DD_AGENT_HOST": "mars", "DD_TRACE_AGENT_URL": None})
def test_trace_url_with_host():
    # with host set by user, and default UDS unavailable, we choose user settings
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=False):
        assert agent.get_trace_url() == "http://mars:8126", agent.get_trace_url()


@pytest.mark.subprocess(env={"DD_AGENT_HOST": "mars", "DD_TRACE_AGENT_PORT": "1235", "DD_TRACE_AGENT_URL": None})
def test_trace_url_with_host_and_port():
    # with host and port set by user, and default UDS unavailable, we choose user settings
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=False):
        assert agent.get_trace_url() == "http://mars:1235"


@pytest.mark.subprocess(env={"DD_TRACE_AGENT_PORT": "1235", "DD_TRACE_AGENT_URL": None})
def test_trace_url_with_uds_and_port():
    # with port set by user, and default UDS available, we choose user settings
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=True):
        assert agent.get_trace_url() == "http://localhost:1235"


@pytest.mark.subprocess(env={"DD_AGENT_HOST": "mars", "DD_TRACE_AGENT_URL": None})
def test_trace_url_with_uds_and_host():
    # with host set by user, and default UDS available, we choose user settings
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=True):
        assert agent.get_trace_url() == "http://mars:8126"


@pytest.mark.subprocess(env={"DD_TRACE_AGENT_PORT": "1235", "DD_AGENT_HOST": "mars", "DD_TRACE_AGENT_URL": None})
def test_trace_url_with_uds_host_and_port():
    # with host and port set by user, and default UDS available, we choose user settings
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=True):
        assert agent.get_trace_url() == "http://mars:1235"


@pytest.mark.subprocess(
    env={"DD_TRACE_AGENT_PORT": "1235", "DD_AGENT_HOST": "mars", "DD_TRACE_AGENT_URL": "http://saturn:1111"}
)
def test_trace_url_with_uds_url_host_and_port():
    # with port, host, and url set by user, and default UDS available, we choose url
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=True):
        assert agent.get_trace_url() == "http://saturn:1111"


@pytest.mark.subprocess(
    env={"DD_AGENT_HOST": "mars", "DD_TRACE_AGENT_PORT": "1235", "DD_TRACE_AGENT_URL": "http://saturn:1111"}
)
def test_trace_url_with_url_host_and_port():
    # with port, host, and url set by user, and default UDS unavailable, we choose url
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=False):
        assert agent.get_trace_url() == "http://saturn:1111"


@pytest.mark.subprocess(env={"DD_DOGSTATSD_URL": None})
def test_stats_url_default():
    # with nothing set by user, and the default UDS unavailable, we choose default http address
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=False):
        assert agent.get_stats_url() == "udp://localhost:8125"


@pytest.mark.subprocess(env={"DD_DOGSTATSD_PORT": "1235", "DD_DOGSTATSD_URL": None})
def test_stats_url_with_port():
    # with port set by user, and default UDS unavailable, we choose user settings
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=False):
        assert agent.get_stats_url() == "udp://localhost:1235"


@pytest.mark.subprocess(env={"DD_AGENT_HOST": "mars", "DD_DOGSTATSD_URL": None})
def test_stats_url_with_host():
    # with host set by user, and default UDS unavailable, we choose user settings
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=False):
        assert agent.get_stats_url() == "udp://mars:8125"


@pytest.mark.subprocess(env={"DD_AGENT_HOST": "mars", "DD_DOGSTATSD_PORT": "1235", "DD_DOGSTATSD_URL": None})
def test_stats_url_with_host_and_port():
    # with host and port set by user, and default UDS unavailable, we choose user settings
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=False):
        assert agent.get_stats_url() == "udp://mars:1235"


@pytest.mark.subprocess(env={"DD_DOGSTATSD_PORT": "1235", "DD_DOGSTATSD_URL": None})
def test_stats_url_with_uds_and_port():
    # with port set by user, and default UDS available, we choose user settings
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=True):
        assert agent.get_stats_url() == "udp://localhost:1235"


@pytest.mark.subprocess(env={"DD_AGENT_HOST": "mars", "DD_DOGSTATSD_URL": None})
def test_stats_url_with_uds_and_host():
    # with host set by user, and default UDS available, we choose user settings
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=True):
        assert agent.get_stats_url() == "udp://mars:8125"


@pytest.mark.subprocess(env={"DD_DOGSTATSD_PORT": "1235", "DD_AGENT_HOST": "mars", "DD_DOGSTATSD_URL": None})
def test_stats_url_with_uds_host_and_port():
    # with host and port set by user, and default UDS available, we choose user settings
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=True):
        assert agent.get_stats_url() == "udp://mars:1235"


@pytest.mark.subprocess(
    env={"DD_DOGSTATSD_PORT": "1235", "DD_AGENT_HOST": "mars", "DD_DOGSTATSD_URL": "udp://saturn:1111"}
)
def test_stats_url_with_uds_url_host_and_port():
    # with port, host, and url set by user, and default UDS available, we choose url
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=True):
        assert agent.get_stats_url() == "udp://saturn:1111"


@pytest.mark.subprocess(
    env={"DD_AGENT_HOST": "mars", "DD_DOGSTATSD_PORT": "1235", "DD_DOGSTATSD_URL": "udp://saturn:1111"}
)
def test_stats_url_with_url_host_and_port():
    # with port, host, and url set by user, and default UDS unavailable, we choose url
    import mock

    from ddtrace.internal import agent

    with mock.patch("os.path.exists", return_value=False):
        assert agent.get_stats_url() == "udp://saturn:1111"


def test_get_connection():
    with pytest.raises(ValueError):
        agent.get_connection("bad://localhost:1234", timeout=1)

    with pytest.raises(ValueError):
        agent.get_connection(":#@$@!//localhost:1234", timeout=1)

    with pytest.raises(ValueError):
        agent.get_connection("", timeout=1)


def test_verify_url():
    verify_url("http://localhost:1234")
    verify_url("https://localhost:1234")
    verify_url("https://localhost")
    verify_url("http://192.168.1.1")
    verify_url("http://[2001:db8:3333:4444:CCCC:DDDD:EEEE:FFFF]")
    verify_url("http://[2001:db8:3333:4444:CCCC:DDDD:EEEE:FFFF]:1234")
    verify_url("unix:///file.sock")
    verify_url("unix:///file")

    with pytest.raises(ValueError) as e:
        verify_url("unix://")
    assert str(e.value) == "Invalid file path in intake URL 'unix://'"

    with pytest.raises(ValueError) as e:
        verify_url("http2://localhost:1234")
    assert (
        str(e.value)
        == "Unsupported protocol 'http2' in intake URL 'http2://localhost:1234'. Must be one of: http, https, unix"
    )

    with pytest.raises(ValueError) as e:
        verify_url("adsf")
    assert str(e.value) == "Unsupported protocol '' in intake URL 'adsf'. Must be one of: http, https, unix"

    with pytest.raises(ValueError) as e:
        verify_url("http://")
    assert str(e.value) == "Invalid hostname in intake URL 'http://'"

    with pytest.raises(ValueError) as e:
        verify_url("https://")
    assert str(e.value) == "Invalid hostname in intake URL 'https://'"

    with pytest.raises(ValueError) as e:
        verify_url("unix://")
    assert str(e.value) == "Invalid file path in intake URL 'unix://'"


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
