"""Reusable HTTP server conformance tests, collected through integration subclasses."""

from dataclasses import FrozenInstanceError
from dataclasses import fields
from typing import Any
from typing import Callable
from typing import Protocol
from typing import cast

import pytest

from ddtrace import config
from ddtrace.contrib._events.http import HTTP_REQUEST_DATA_EVENTS
from ddtrace.contrib._events.http import HttpRequestBlock
from ddtrace.contrib._events.http import HttpRequestBlockCheckEvent
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.internal import core


class HttpContractAdapter(Protocol):
    """Drive a real instrumented app; never synthesize contract events.

    route registers /items/{name} in framework-native syntax. Handlers receive
    the name argument and return text or a response made by response. request
    consumes and closes the response before returning its bytes, including for
    non-success statuses. Async frameworks may drive their test client here.
    """

    route_pattern: str

    def route(self) -> Callable[[Callable[..., Any]], Any]: ...

    def response(self, body: str, status: int = 200) -> Any: ...

    def request(self, path: str = "/items/one", method: str = "GET") -> bytes: ...


@pytest.fixture(name="observations")
def observe_http_requests():
    observed = []

    def observe(event):
        observed.append(event)

    names = [event.event_name for event in HTTP_REQUEST_DATA_EVENTS.values()]
    for name in names:
        core.on(name, observe)
    try:
        yield observed
    finally:
        for name in names:
            core.reset_listeners(name, observe)


@pytest.fixture(name="block_checker")
def register_http_block_checker():
    listeners = []

    def register(listener):
        listeners.append(listener)
        core.on(HttpRequestBlockCheckEvent.event_name, listener)

    yield register
    for listener in listeners:
        core.reset_listeners(HttpRequestBlockCheckEvent.event_name, listener)


def block_response(body=b"blocked", status=403):
    return HttpRequestBlock(status, (("Content-Type", "text/plain"),), body)


class HttpContractTests:
    """Inherit in a Test-prefixed class and provide the http_adapter fixture."""

    def test_phase_order_and_field_availability(self, http_adapter: HttpContractAdapter, observations):
        @http_adapter.route()
        def handler(name):
            assert observations[-1].phase == HttpRequestPhase.BEFORE_HANDLER
            assert not observations[-1].handler_started
            return http_adapter.response("done", status=201)

        assert http_adapter.request("/items/one?tag=a&tag=b&empty=") == b"done"
        assert [observation.phase for observation in observations] == list(HttpRequestPhase)
        first, route, before, ready, committed, completed = observations
        assert all(observation.request_id is first.request_id for observation in observations)
        assert first.request.route is None
        assert first.response is None
        assert first.request.method == "GET"
        assert first.request.query_string == "tag=a&tag=b&empty="
        assert first.request.query_parameters == (("tag", "a"), ("tag", "b"), ("empty", ""))
        assert first.request.body is None
        assert route.request.route == http_adapter.route_pattern
        assert not route.handler_started
        assert ready.handler_started
        assert ready.response.status_code == 201
        assert not ready.response_committed
        assert committed.response_committed
        assert completed.response_committed
        assert completed.errors == ()
        assert not {"resource", "service", "integration_config", "operation_name"} & {f.name for f in fields(first)}

    @pytest.mark.parametrize(
        "phase",
        [
            HttpRequestPhase.REQUEST_STARTED,
            HttpRequestPhase.ROUTE_RESOLVED,
            HttpRequestPhase.BEFORE_HANDLER,
            HttpRequestPhase.RESPONSE_READY,
        ],
    )
    def test_block_at_supported_boundary(self, http_adapter: HttpContractAdapter, observations, block_checker, phase):
        calls = []

        @http_adapter.route()
        def handler(name):
            calls.append(name)
            return "original"

        def inspect(event):
            if event.phase == phase:
                raise block_response()

        block_checker(inspect)
        assert http_adapter.request() == b"blocked"
        assert calls == (["one"] if phase == HttpRequestPhase.RESPONSE_READY else [])
        assert observations[-1].response.status_code == 403
        assert observations[-1].errors == ()

    def test_empty_query_is_available(self, http_adapter: HttpContractAdapter, observations):
        @http_adapter.route()
        def handler(name):
            return "done"

        http_adapter.request()
        assert observations[0].request.query_string == ""
        assert observations[0].request.query_parameters == ()
        assert observations[0].request.body is None

    def test_data_consumer_without_tracing(
        self, http_adapter: HttpContractAdapter, observations, tracer, test_spans, monkeypatch
    ):
        monkeypatch.setattr(tracer, "enabled", False)

        @http_adapter.route()
        def handler(name):
            assert tracer.current_span() is None
            return "done"

        assert http_adapter.request() == b"done"
        assert [observation.phase for observation in observations] == list(HttpRequestPhase)
        assert observations[-1].request.route == http_adapter.route_pattern
        assert test_spans.pop() == []

    def test_route_miss_has_no_handler_phase(self, http_adapter: HttpContractAdapter, observations):
        http_adapter.request("/missing")
        assert [observation.phase for observation in observations] == [
            HttpRequestPhase.REQUEST_STARTED,
            HttpRequestPhase.RESPONSE_READY,
            HttpRequestPhase.RESPONSE_COMMITTED,
            HttpRequestPhase.COMPLETED,
        ]
        assert observations[-1].request.route is None
        assert not observations[-1].handler_started
        assert observations[-1].response.status_code == 404

    def test_block_check_without_tracing_or_data_listener(
        self, http_adapter: HttpContractAdapter, block_checker, tracer, monkeypatch, test_spans
    ):
        monkeypatch.setattr(tracer, "enabled", False)

        @http_adapter.route()
        def handler(name):
            pytest.fail("blocked handler was invoked")

        def block(event):
            raise block_response()

        block_checker(block)
        assert http_adapter.request() == b"blocked"
        assert test_spans.pop() == []

    @pytest.mark.parametrize("failure", [RuntimeError, TimeoutError])
    def test_block_check_failure_allows_request(
        self, http_adapter: HttpContractAdapter, block_checker, failure, monkeypatch
    ):
        # The suite enables DD_TESTING_RAISE; exercise production listener isolation.
        monkeypatch.setattr(config, "_raise", False)

        @http_adapter.route()
        def handler(name):
            return "original"

        def inspect(event):
            raise failure("block_check failed")

        block_checker(inspect)
        assert http_adapter.request() == b"original"

    @pytest.mark.parametrize(
        "candidate",
        [
            HttpRequestBlock(99),
            HttpRequestBlock(204, body=b"bad"),
            HttpRequestBlock(headers=(("X-Test", "bad\r\n"),)),
            HttpRequestBlock(headers=(("Content-Length", "99"),)),
            HttpRequestBlock(headers=(("Transfer-Encoding", "chunked"),)),
            HttpRequestBlock(body=cast(Any, "not bytes")),
        ],
    )
    def test_invalid_blocks_allow_request(self, http_adapter: HttpContractAdapter, block_checker, candidate):
        @http_adapter.route()
        def handler(name):
            return "original"

        def check(event):
            if event.phase == HttpRequestPhase.BEFORE_HANDLER:
                raise candidate

        block_checker(check)
        assert http_adapter.request() == b"original"

    def test_first_block_skips_remaining_checks_and_preserves_observations(
        self, http_adapter: HttpContractAdapter, block_checker, observations
    ):
        calls = []

        def first(event):
            calls.append("first")
            raise block_response()

        def second(event):
            calls.append("second")
            raise block_response(b"second")

        block_checker(first)
        block_checker(second)
        assert http_adapter.request() == b"blocked"
        assert calls == ["first"]
        assert [event.phase for event in observations] == [
            HttpRequestPhase.REQUEST_STARTED,
            HttpRequestPhase.RESPONSE_READY,
            HttpRequestPhase.RESPONSE_COMMITTED,
            HttpRequestPhase.COMPLETED,
        ]

    @pytest.mark.parametrize("phase", [HttpRequestPhase.REQUEST_STARTED, HttpRequestPhase.RESPONSE_READY])
    def test_head_block_has_no_body(self, http_adapter: HttpContractAdapter, observations, block_checker, phase):
        @http_adapter.route()
        def handler(name):
            return "original"

        def inspect(event):
            if event.phase == phase:
                raise block_response()

        block_checker(inspect)
        assert http_adapter.request(method="HEAD") == b""
        assert observations[-1].response.status_code == 403
        assert {name.lower(): value for name, value in observations[-1].response.headers}["content-length"] == "7"

    def test_listener_failure_does_not_skip_later_blocker(
        self, http_adapter: HttpContractAdapter, block_checker, monkeypatch
    ):
        monkeypatch.setattr(config, "_raise", False)
        calls = []

        def failed(event):
            calls.append("failed")
            raise TimeoutError("consumer failed")

        def following(event):
            calls.append("following")
            raise block_response()

        block_checker(failed)
        block_checker(following)
        assert http_adapter.request() == b"blocked"
        assert calls == ["failed", "following"]

    def test_process_control_exception_propagates_and_completes(
        self, http_adapter: HttpContractAdapter, block_checker, observations
    ):
        def check(event):
            raise KeyboardInterrupt()

        block_checker(check)
        with pytest.raises(KeyboardInterrupt):
            http_adapter.request()
        assert observations[-1].phase == HttpRequestPhase.COMPLETED

    def test_blocked_response_keeps_duplicate_headers(
        self, http_adapter: HttpContractAdapter, block_checker, observations
    ):
        def check(event):
            raise HttpRequestBlock(headers=(("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")), body=b"blocked")

        block_checker(check)
        assert http_adapter.request() == b"blocked"
        assert [v for k, v in observations[-1].response.headers if k.lower() == "set-cookie"] == ["a=1", "b=2"]

    def test_observation_mutation_does_not_change_request_state(self, http_adapter: HttpContractAdapter, observations):
        @http_adapter.route()
        def handler(name):
            observations[0].handler_started = True
            observations[0].request = None
            return "done"

        assert http_adapter.request() == b"done"
        assert observations[-1].request.method == "GET"
        assert observations[1].request.route == http_adapter.route_pattern
        assert not observations[1].handler_started
        assert len({id(event) for event in observations}) == len(observations)

    def test_observation_values_are_frozen(self, http_adapter: HttpContractAdapter, observations):
        @http_adapter.route()
        def handler(name):
            return "done"

        assert http_adapter.request() == b"done"
        first = observations[0]
        ready = next(event for event in observations if event.phase == HttpRequestPhase.RESPONSE_READY)
        assert first.request.route is None
        assert first.response is None
        with pytest.raises(FrozenInstanceError):
            first.request.method = "DELETE"
        with pytest.raises(FrozenInstanceError):
            ready.response.status_code = 500
