import socket
import typing
from unittest import mock

import pytest

from ddtrace.internal.openfeature._agentless import api_key_fingerprint
from ddtrace.internal.openfeature._evp_transport import DIRECT_RETRY_STATUSES
from ddtrace.internal.openfeature._evp_transport import FeatureFlagEVPRouteSelector
from ddtrace.internal.openfeature._evp_transport import get_evp_connection
from ddtrace.internal.settings.openfeature import AGENTLESS
from ddtrace.internal.settings.openfeature import REMOTE_CONFIG


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status


def _selector(source: str = AGENTLESS, endpoints: tuple[str, ...] = (), api_key: typing.Optional[str] = "secret"):
    calls = []

    def info_provider(url: str):
        calls.append(url)
        return {"endpoints": endpoints}

    selector = FeatureFlagEVPRouteSelector(
        configuration_source=source,
        agent_url="http://agent:8126",
        api_key=api_key,
        site="datadoghq.com",
        info_provider=info_provider,
    )
    return selector, calls


@pytest.mark.parametrize(
    ("api_key", "expected"),
    [
        ("", "rijn_RZwTDmWjELXeEmMEb0eIIegKayGGUPNsuJweEPhlXi5"),
        ("padding-171", "rijn_053ybBRXypQt9AC6UIlqH1YCFYSV1rQl8HCDIcBZs3D"),
        ("!@#$%^𐍈한€हИ£", "rijn_eFLHeyLxwaiNs2hY16pjkjNjVSHWRgf2rlveKc8YA1K"),
    ],
)
def test_api_key_fingerprint_matches_clifford_v1(api_key, expected):
    assert api_key_fingerprint(api_key) == expected
    assert len(api_key_fingerprint(api_key)) == 48


def test_local_route_prefers_v4_and_never_carries_direct_credentials():
    selector, info_calls = _selector(endpoints=("/evp_proxy/v2/", "/evp_proxy/v4/"))

    route = selector.select()

    assert route is not None
    assert route.base_path == "/evp_proxy/v4"
    assert route.headers == {"X-Datadog-EVP-Subdomain": "event-platform-intake"}
    assert route.fallback is not None
    assert route.fallback.intake == "https://event-platform-intake.datadoghq.com"
    assert route.fallback.headers == {
        "DD-API-KEY": "secret",
        "DD-API-KEY-FINGERPRINT": "rijn_amLaG4Pd6h6t9VtJna81k744P1DYxGHzIJ6ECO3OOMj",
    }
    assert selector.select() is route
    assert info_calls == ["http://agent:8126"]


def test_agentless_uses_direct_when_discovery_fails_before_send():
    selector, _ = _selector(endpoints=())

    route = selector.select()

    assert route is not None
    assert route.direct is True
    assert route.base_path == ""


def test_remote_configuration_never_uses_direct_intake():
    selector, _ = _selector(source=REMOTE_CONFIG, endpoints=())

    assert selector.select() is None


def test_direct_https_uses_datadog_proxy(monkeypatch):
    monkeypatch.setenv("DD_PROXY_HTTPS", "http://proxy.example.test:8443")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    selector, _ = _selector(endpoints=())
    route = selector.select()
    assert route is not None
    base_connection_factory = mock.Mock()
    proxy_connection = mock.Mock()

    with mock.patch(
        "ddtrace.internal.openfeature._evp_transport.httplib.HTTPSConnection", return_value=proxy_connection
    ) as https_connection:
        connection = get_evp_connection(route, 2.0, base_connection_factory)

    assert connection is proxy_connection
    https_connection.assert_called_once_with("proxy.example.test", 8443, timeout=2.0)
    proxy_connection.set_tunnel.assert_called_once_with("event-platform-intake.datadoghq.com", 443, headers={})
    base_connection_factory.assert_not_called()


def test_direct_https_uses_standard_proxy(monkeypatch):
    monkeypatch.delenv("DD_PROXY_HTTPS", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://standard-proxy.example.test:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    selector, _ = _selector(endpoints=())
    route = selector.select()
    assert route is not None

    with mock.patch("ddtrace.internal.openfeature._evp_transport.httplib.HTTPSConnection") as https_connection:
        get_evp_connection(route, 1.5, mock.Mock())

    https_connection.assert_called_once_with("standard-proxy.example.test", 8080, timeout=1.5)


def test_direct_https_honors_no_proxy(monkeypatch):
    monkeypatch.setenv("DD_PROXY_HTTPS", "http://proxy.example.test:8443")
    monkeypatch.setenv("NO_PROXY", ".datadoghq.com")
    selector, _ = _selector(endpoints=())
    route = selector.select()
    assert route is not None
    direct_connection = mock.Mock()
    base_connection_factory = mock.Mock(return_value=direct_connection)

    connection = get_evp_connection(route, 2.0, base_connection_factory)

    assert connection is direct_connection
    base_connection_factory.assert_called_once_with(route.intake, timeout=2.0)


def test_local_route_never_uses_direct_proxy(monkeypatch):
    monkeypatch.setenv("DD_PROXY_HTTPS", "http://proxy.example.test:8443")
    selector, _ = _selector(endpoints=("/evp_proxy/v4/",))
    route = selector.select()
    assert route is not None
    local_connection = mock.Mock()
    base_connection_factory = mock.Mock(return_value=local_connection)

    connection = get_evp_connection(route, 2.0, base_connection_factory)

    assert connection is local_connection
    base_connection_factory.assert_called_once_with("http://agent:8126", timeout=2.0)


@pytest.mark.parametrize("status", sorted(DIRECT_RETRY_STATUSES))
def test_definitive_http_rejection_replays_direct_and_makes_route_sticky(status):
    selector, _ = _selector(endpoints=("/evp_proxy/v2/",))
    route = selector.select()
    assert route is not None
    calls = []

    def send_once(active_route):
        calls.append(active_route)
        return _Response(202 if active_route.direct else status)

    response = selector.send(route, send_once)

    assert response.status == 202
    assert [call.direct for call in calls] == [False, True]
    assert selector.select() is route.fallback


@pytest.mark.parametrize(
    "error",
    [
        ConnectionRefusedError(),
        FileNotFoundError(),
        socket.gaierror(socket.EAI_AGAIN, "try again"),
        socket.gaierror(socket.EAI_NONAME, "not found"),
    ],
)
def test_definitive_connection_failure_replays_direct(error):
    selector, _ = _selector(endpoints=("/evp_proxy/v2/",))
    route = selector.select()
    assert route is not None
    calls = []

    def send_once(active_route):
        calls.append(active_route)
        if not active_route.direct:
            raise error
        return _Response(202)

    response = selector.send(route, send_once)

    assert response.status == 202
    assert [call.direct for call in calls] == [False, True]


@pytest.mark.parametrize("error", [ConnectionResetError(), BrokenPipeError(), TimeoutError()])
def test_ambiguous_failure_does_not_replay_current_batch_but_switches_future(error):
    selector, _ = _selector(endpoints=("/evp_proxy/v2/",))
    route = selector.select()
    assert route is not None
    calls = []

    def send_once(active_route):
        calls.append(active_route)
        raise error

    with pytest.raises(type(error)):
        selector.send(route, send_once)

    assert calls == [route]
    assert selector.select() is route.fallback


@pytest.mark.parametrize("status", [429, 500, 503])
def test_overload_and_server_errors_do_not_trigger_direct_fallback(status):
    selector, _ = _selector(endpoints=("/evp_proxy/v2/",))
    route = selector.select()
    assert route is not None
    calls = []

    response = selector.send(route, lambda active_route: calls.append(active_route) or _Response(status))

    assert response.status == status
    assert calls == [route]
    assert selector.select() is route
