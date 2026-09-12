import os
import select
import signal
import socket
import threading
import typing
from unittest import mock

import pytest

from ddtrace.internal.constants import _HTTPLIB_NO_TRACE_REQUEST
from ddtrace.internal.openfeature import _evp_transport
from ddtrace.internal.openfeature._evp_transport import DIRECT_RETRY_STATUSES
from ddtrace.internal.openfeature._evp_transport import EVP_ORIGIN_HEADERS
from ddtrace.internal.openfeature._evp_transport import AmbiguousLocalEVPDeliveryError
from ddtrace.internal.openfeature._evp_transport import FeatureFlagEVPRouteSelector
from ddtrace.internal.openfeature._evp_transport import get_evp_connection
from ddtrace.internal.openfeature._evp_transport import get_feature_flag_evp_route_selector
from ddtrace.internal.openfeature._evp_transport import reset_feature_flag_evp_route_selector
from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
from ddtrace.internal.openfeature.writer import ExposureWriter
from ddtrace.internal.settings.openfeature import AGENTLESS
from ddtrace.internal.settings.openfeature import REMOTE_CONFIG


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status


def _agent_info(endpoints):
    return {
        "endpoints": endpoints,
        "evp_proxy_allowed_headers": tuple(EVP_ORIGIN_HEADERS),
    }


def _selector(
    source: str = AGENTLESS,
    endpoints: tuple[str, ...] = (),
    api_key: typing.Optional[str] = "secret",
    site: str = "datadoghq.com",
    clock: typing.Callable[[], float] = lambda: 0.0,
    recovery_interval: float = 30.0,
    agent_url: str = "http://agent:8126",
):
    calls = []

    def info_provider(url: str):
        calls.append(url)
        return _agent_info(endpoints)

    selector = FeatureFlagEVPRouteSelector(
        configuration_source=source,
        agent_url=agent_url,
        api_key=api_key,
        site=site,
        info_provider=info_provider,
        clock=clock,
        recovery_interval=recovery_interval,
    )
    return selector, calls


def test_local_route_prefers_v4_and_never_carries_direct_credentials():
    selector, info_calls = _selector(endpoints=("/evp_proxy/v2/", "/evp_proxy/v4/"))

    route = selector.select()

    assert route is not None
    assert route.base_path == "/evp_proxy/v4"
    assert route.headers == {
        "Content-Type": "application/json",
        "X-Datadog-EVP-Subdomain": "event-platform-intake",
        **EVP_ORIGIN_HEADERS,
    }
    assert route.fallback is not None
    assert route.fallback.intake == "https://event-platform-intake.datadoghq.com"
    assert route.fallback.headers == {
        "Content-Type": "application/json",
        "DD-API-KEY": "secret",
        **EVP_ORIGIN_HEADERS,
    }
    assert selector.select() is route
    assert info_calls == ["http://agent:8126"]


@pytest.mark.parametrize(
    ("agent_url", "expected_base_path"),
    [
        ("http://gateway:8126/datadog/", "/datadog/evp_proxy/v4"),
        ("https://gateway:8126/datadog/nested", "/datadog/nested/evp_proxy/v4"),
        ("unix:///var/run/datadog/apm.socket", "/evp_proxy/v4"),
    ],
)
def test_local_route_preserves_http_agent_path_prefix_but_not_unix_socket_path(agent_url, expected_base_path):
    selector, info_calls = _selector(endpoints=("/evp_proxy/v4/",), agent_url=agent_url)

    route = selector.select()

    assert route is not None
    assert route.intake == agent_url
    assert route.base_path == expected_base_path
    assert route.endpoint("/api/v2/exposures") == expected_base_path + "/api/v2/exposures"
    assert info_calls == [agent_url]


def test_agentless_uses_direct_when_discovery_fails_before_send():
    selector, _ = _selector(endpoints=())

    route = selector.select()

    assert route is not None
    assert route.direct is True
    assert route.base_path == ""


def test_discovery_requires_an_exact_advertised_proxy_path():
    selector, _ = _selector(endpoints=("/evp_proxy/v4-unsupported", "/evp_proxy/v2/extra"))

    route = selector.select()

    assert route is not None
    assert route.direct is True


@pytest.mark.parametrize(
    "agent_info",
    [
        {"endpoints": ("/evp_proxy/v4/",)},
        {"endpoints": ("/evp_proxy/v4/",), "evp_proxy_allowed_headers": None},
        {"endpoints": ("/evp_proxy/v4/",), "evp_proxy_allowed_headers": ("DD-EVP-ORIGIN",)},
        {"endpoints": ("/evp_proxy/v4/",), "evp_proxy_allowed_headers": ("DD-EVP-ORIGIN-VERSION",)},
    ],
)
def test_agentless_uses_direct_when_local_proxy_cannot_forward_identity_headers(agent_info):
    selector = FeatureFlagEVPRouteSelector(
        configuration_source=AGENTLESS,
        agent_url="http://agent:8126",
        api_key="secret",
        site="datadoghq.com",
        info_provider=lambda _: agent_info,
    )

    route = selector.select()

    assert route is not None
    assert route.direct is True


def test_local_proxy_header_capabilities_are_case_insensitive():
    selector = FeatureFlagEVPRouteSelector(
        configuration_source=AGENTLESS,
        agent_url="http://agent:8126",
        api_key="secret",
        site="datadoghq.com",
        info_provider=lambda _: {
            "endpoints": ("/evp_proxy/v4/",),
            "evp_proxy_allowed_headers": ("dd-evp-origin-version", "dd-evp-origin"),
        },
    )

    route = selector.select()

    assert route is not None
    assert route.direct is False
    assert route.base_path == "/evp_proxy/v4"


def test_agentless_direct_route_accepts_custom_hostname_domain():
    selector, _ = _selector(endpoints=(), site="  CUSTOM.REGION.example-test.com  ")

    route = selector.select()

    assert route is not None
    assert route.intake == "https://event-platform-intake.custom.region.example-test.com"
    assert route.headers == {
        "Content-Type": "application/json",
        "DD-API-KEY": "secret",
        **EVP_ORIGIN_HEADERS,
    }


@pytest.mark.parametrize(
    "site",
    [
        "datadoghq.com@attacker.example",
        "datadoghq.com:password@attacker.example",
        "datadoghq.com:443",
        "https://attacker.example",
        "datadoghq.com/path",
        "datadoghq.com?redirect=attacker.example",
        "datadoghq.com#attacker.example",
        "data doghq.com",
        "datadoghq.com\n.attacker.example",
        "datadoghq.com\\attacker.example",
        "datadoghq.com%2eattacker.example",
        "datadoghq.com。attacker.example",
        "datadoghq.com．attacker.example",
        "datadoghq.com｡attacker.example",
    ],
)
def test_agentless_direct_route_rejects_host_confusion(site):
    selector, _ = _selector(endpoints=(), site=site)

    assert selector.select() is None


@pytest.mark.parametrize("site", ["datadoghq.com@attacker.example", "datadoghq.com%2eattacker.example"])
def test_invalid_direct_site_does_not_attach_api_key_to_local_route(site):
    selector, _ = _selector(endpoints=("/evp_proxy/v4/",), site=site)

    route = selector.select()

    assert route is not None
    assert route.headers == {
        "Content-Type": "application/json",
        "X-Datadog-EVP-Subdomain": "event-platform-intake",
        **EVP_ORIGIN_HEADERS,
    }
    assert route.fallback is None


def test_invalid_direct_site_warns_once_without_leaking_configuration():
    selector, _ = _selector(endpoints=(), api_key="must-not-leak", site="sensitive.invalid/path")

    with mock.patch("ddtrace.internal.openfeature._evp_transport.log") as logger:
        assert selector.select() is None
        assert selector.select() is None

    invalid_warning = "Feature Flagging direct event delivery disabled because DD_SITE is invalid"
    assert sum(call.args == (invalid_warning,) for call in logger.warning.call_args_list) == 1
    messages = " ".join(str(call) for call in logger.warning.call_args_list)
    assert "must-not-leak" not in messages
    assert "sensitive.invalid/path" not in messages


def test_default_writers_share_one_route_selector():
    reset_feature_flag_evp_route_selector()
    try:
        exposure_writer = ExposureWriter(enabled=False)
        flag_evaluation_writer = FlagEvaluationWriter()

        assert exposure_writer._route_selector is flag_evaluation_writer._route_selector
    finally:
        reset_feature_flag_evp_route_selector()


def test_remote_configuration_keeps_historical_fixed_v2_without_discovery_or_direct_fallback():
    selector, info_calls = _selector(
        source=REMOTE_CONFIG,
        endpoints=(),
        api_key="must-not-be-used",
        agent_url="http://gateway:8126/datadog/",
    )

    route = selector.select()

    assert route is not None
    assert route.base_path == "/datadog/evp_proxy/v2"
    assert route.direct is False
    assert route.fallback is None
    assert "DD-API-KEY" not in route.headers
    assert info_calls == []

    response = selector.send(route, lambda _: _Response(404))
    assert response.status == 404
    assert selector.select() is route
    assert info_calls == []


def test_unavailable_route_recovers_after_cooldown():
    now = [10.0]
    endpoints = []
    info_calls = []

    def info_provider(url):
        info_calls.append(url)
        return _agent_info(tuple(endpoints))

    selector = FeatureFlagEVPRouteSelector(
        configuration_source=AGENTLESS,
        agent_url="http://agent:8126",
        api_key=None,
        site="datadoghq.com",
        info_provider=info_provider,
        clock=lambda: now[0],
        recovery_interval=30.0,
    )

    assert selector.select() is None
    assert selector.select() is None
    assert len(info_calls) == 1

    endpoints.append("/evp_proxy/v4/")
    now[0] = 39.9
    assert selector.select() is None
    assert len(info_calls) == 1

    now[0] = 40.0
    route = selector.select()
    assert route is not None
    assert route.base_path == "/evp_proxy/v4"
    assert len(info_calls) == 2


def test_failed_local_route_without_direct_credentials_recovers_after_cooldown():
    now = [10.0]
    endpoints = ["/evp_proxy/v2/"]
    selector = FeatureFlagEVPRouteSelector(
        configuration_source=AGENTLESS,
        agent_url="http://agent:8126",
        api_key=None,
        site="datadoghq.com",
        info_provider=lambda _: _agent_info(tuple(endpoints)),
        clock=lambda: now[0],
        recovery_interval=30.0,
    )
    route = selector.select()
    assert route is not None

    def fail_ambiguously(_):
        raise ConnectionResetError("ambiguous")

    with pytest.raises(ConnectionResetError):
        selector.send(route, fail_ambiguously)

    assert selector.select() is None
    endpoints[:] = ["/evp_proxy/v4/"]
    now[0] = 39.9
    assert selector.select() is None
    now[0] = 40.0
    recovered = selector.select()
    assert recovered is not None
    assert recovered.base_path == "/evp_proxy/v4"


def test_definitive_local_failure_without_direct_credentials_enters_cooldown():
    now = [10.0]
    selector = FeatureFlagEVPRouteSelector(
        configuration_source=AGENTLESS,
        agent_url="http://agent:8126",
        api_key=None,
        site="datadoghq.com",
        info_provider=lambda _: _agent_info(("/evp_proxy/v2/",)),
        clock=lambda: now[0],
        recovery_interval=30.0,
    )
    route = selector.select()
    assert route is not None

    def refuse_connection(_):
        raise ConnectionRefusedError()

    with pytest.raises(ConnectionRefusedError):
        selector.send(route, refuse_connection)

    assert selector.select() is None


def test_rejected_local_route_without_direct_credentials_recovers_after_cooldown():
    now = [10.0]
    endpoints = ["/evp_proxy/v2/"]
    selector = FeatureFlagEVPRouteSelector(
        configuration_source=AGENTLESS,
        agent_url="http://agent:8126",
        api_key=None,
        site="datadoghq.com",
        info_provider=lambda _: _agent_info(tuple(endpoints)),
        clock=lambda: now[0],
        recovery_interval=30.0,
    )
    route = selector.select()
    assert route is not None

    response = selector.send(route, lambda _: _Response(404))

    assert response.status == 404
    assert selector.select() is None
    endpoints[:] = ["/evp_proxy/v4/"]
    now[0] = 40.0
    recovered = selector.select()
    assert recovered is not None
    assert recovered.base_path == "/evp_proxy/v4"


def test_direct_route_is_sticky_and_never_reprobes_local():
    endpoints = []
    info_calls = []

    def info_provider(url):
        info_calls.append(url)
        return _agent_info(tuple(endpoints))

    selector = FeatureFlagEVPRouteSelector(
        configuration_source=AGENTLESS,
        agent_url="http://agent:8126",
        api_key="secret",
        site="datadoghq.com",
        info_provider=info_provider,
        clock=lambda: 1000.0,
        recovery_interval=1.0,
    )

    route = selector.select()
    assert route is not None and route.direct
    endpoints.append("/evp_proxy/v4/")
    assert selector.select() is route
    assert info_calls == ["http://agent:8126"]


def test_concurrent_first_selection_serializes_one_discovery():
    entered = threading.Event()
    release = threading.Event()
    info_calls = []

    def info_provider(url):
        info_calls.append(url)
        entered.set()
        assert release.wait(timeout=2.0)
        return _agent_info(("/evp_proxy/v4/",))

    selector = FeatureFlagEVPRouteSelector(
        configuration_source=AGENTLESS,
        agent_url="http://agent:8126",
        api_key=None,
        site="datadoghq.com",
        info_provider=info_provider,
    )
    routes = []
    threads = [threading.Thread(target=lambda: routes.append(selector.select())) for _ in range(8)]
    for thread in threads:
        thread.start()
    assert entered.wait(timeout=2.0)
    release.set()
    for thread in threads:
        thread.join(timeout=2.0)
        assert not thread.is_alive()

    assert len(info_calls) == 1
    assert len(routes) == 8
    assert all(route is routes[0] for route in routes)


def test_pid_change_resets_route_and_repeats_discovery(monkeypatch):
    pid = [100]
    monkeypatch.setattr("ddtrace.internal.openfeature._evp_transport.os.getpid", lambda: pid[0])
    selector, info_calls = _selector(endpoints=("/evp_proxy/v2/",))
    selector._pid = pid[0]

    parent_route = selector.select()
    pid[0] = 101
    child_route = selector.select()

    assert child_route is not parent_route
    assert len(info_calls) == 2


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
@pytest.mark.parametrize("lock_owner", ["route", "singleton"])
def test_route_selector_locks_are_reinitialized_after_fork(lock_owner):
    reset_feature_flag_evp_route_selector()
    selector, _ = _selector(endpoints=("/evp_proxy/v4/",))
    lock = selector._lock if lock_owner == "route" else _evp_transport._SELECTOR_LOCK
    entered = threading.Event()
    release = threading.Event()

    def hold_lock():
        with lock:
            entered.set()
            assert release.wait(timeout=5.0)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert entered.wait(timeout=2.0)

    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        try:
            if lock_owner == "route":
                assert selector.select() is not None
            else:
                assert get_feature_flag_evp_route_selector() is not None
            os.write(write_fd, b"ok")
        except Exception:
            os.write(write_fd, b"error")
        finally:
            os.close(write_fd)
            os._exit(0)

    os.close(write_fd)
    try:
        release.set()
        holder.join(timeout=2.0)
        assert not holder.is_alive()
        readable, _, _ = select.select([read_fd], [], [], 3.0)
        if not readable:
            os.kill(child_pid, signal.SIGKILL)
        assert readable, "child blocked on a lock inherited from a vanished parent thread"
        assert os.read(read_fd, 2) == b"ok"
    finally:
        os.close(read_fd)
        os.waitpid(child_pid, 0)
        reset_feature_flag_evp_route_selector()


def test_direct_https_uses_datadog_proxy(monkeypatch):
    monkeypatch.setenv("DD_PROXY_HTTPS", "http://proxy.example.test:8443")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    selector, _ = _selector(endpoints=())
    route = selector.select()
    assert route is not None
    proxy_connection = mock.Mock()

    with mock.patch(
        "ddtrace.internal.openfeature._evp_transport.httplib.HTTPSConnection", return_value=proxy_connection
    ) as https_connection:
        connection = get_evp_connection(route, 2.0)

    assert connection is proxy_connection
    assert getattr(connection, _HTTPLIB_NO_TRACE_REQUEST) is True
    https_connection.assert_called_once_with("proxy.example.test", 8443, timeout=2.0)
    proxy_connection.set_tunnel.assert_called_once_with("event-platform-intake.datadoghq.com", 443, headers={})


def test_direct_https_uses_standard_proxy(monkeypatch):
    monkeypatch.delenv("DD_PROXY_HTTPS", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://standard-proxy.example.test:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    selector, _ = _selector(endpoints=())
    route = selector.select()
    assert route is not None

    proxy_connection = mock.Mock()
    with mock.patch(
        "ddtrace.internal.openfeature._evp_transport.httplib.HTTPSConnection", return_value=proxy_connection
    ) as https_connection:
        connection = get_evp_connection(route, 1.5)

    assert getattr(connection, _HTTPLIB_NO_TRACE_REQUEST) is True
    https_connection.assert_called_once_with("standard-proxy.example.test", 8080, timeout=1.5)


def test_direct_https_honors_no_proxy(monkeypatch):
    monkeypatch.setenv("DD_PROXY_HTTPS", "http://proxy.example.test:8443")
    monkeypatch.setenv("NO_PROXY", ".datadoghq.com")
    selector, _ = _selector(endpoints=())
    route = selector.select()
    assert route is not None
    direct_connection = mock.Mock()

    with mock.patch(
        "ddtrace.internal.openfeature._evp_transport.httplib.HTTPSConnection", return_value=direct_connection
    ) as https_connection:
        connection = get_evp_connection(route, 2.0)

    assert connection is direct_connection
    assert getattr(connection, _HTTPLIB_NO_TRACE_REQUEST) is True
    https_connection.assert_called_once_with("event-platform-intake.datadoghq.com", 443, timeout=2.0)


def test_direct_https_without_proxy_uses_non_redirecting_stdlib_connection(monkeypatch):
    monkeypatch.delenv("DD_PROXY_HTTPS", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    selector, _ = _selector(endpoints=())
    route = selector.select()
    assert route is not None
    direct_connection = mock.Mock()

    with mock.patch(
        "ddtrace.internal.openfeature._evp_transport.httplib.HTTPSConnection", return_value=direct_connection
    ) as https_connection:
        connection = get_evp_connection(route, 2.0)

    assert connection is direct_connection
    assert getattr(connection, _HTTPLIB_NO_TRACE_REQUEST) is True
    https_connection.assert_called_once_with("event-platform-intake.datadoghq.com", 443, timeout=2.0)


def test_direct_connection_factory_is_only_an_explicit_test_override(monkeypatch):
    monkeypatch.delenv("DD_PROXY_HTTPS", raising=False)
    selector, _ = _selector(endpoints=())
    route = selector.select()
    assert route is not None
    direct_connection = mock.Mock()
    connection_factory = mock.Mock(return_value=direct_connection)

    connection = get_evp_connection(route, 2.0, connection_factory)

    assert connection is direct_connection
    connection_factory.assert_called_once_with(route.intake, timeout=2.0)


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
        OSError(10061, "connection refused"),
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

    with pytest.raises(AmbiguousLocalEVPDeliveryError) as caught:
        selector.send(route, send_once)

    assert caught.value.__cause__ is error
    assert calls == [route]
    assert selector.select() is route.fallback


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_overload_and_server_errors_do_not_trigger_direct_fallback(status):
    selector, _ = _selector(endpoints=("/evp_proxy/v2/",))
    route = selector.select()
    assert route is not None
    calls = []

    response = selector.send(route, lambda active_route: calls.append(active_route) or _Response(status))

    assert response.status == status
    assert calls == [route]
    assert selector.select() is route
