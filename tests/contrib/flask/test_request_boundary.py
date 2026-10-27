"""Characterize Flask boundaries before connecting the shared request adapter."""

from collections.abc import Iterator
from typing import Any
from typing import Optional

import flask
import pytest
from werkzeug.test import EnvironBuilder


@pytest.fixture
def boundary_app() -> tuple[flask.Flask, list[tuple[str, Any]]]:
    observations: list[tuple[str, Any]] = []

    class ObservedFlask(flask.Flask):
        def preprocess_request(self) -> Any:
            rule = flask.request.url_rule
            observations.append(("route", rule.rule if rule else None))
            return super().preprocess_request()

        def full_dispatch_request(self) -> flask.Response:
            response = super().full_dispatch_request()
            observations.append(("dispatch_return", response.status_code))
            return response

        def finalize_request(self, rv: Any, from_error_handler: bool = False) -> flask.Response:
            response = super().finalize_request(rv, from_error_handler=from_error_handler)
            # Metadata is available here without reading response.response.
            observations.append(("prepared", (response.status_code, response.headers.getlist("X-Boundary"))))
            assert flask.has_request_context()
            return response

    app = ObservedFlask(__name__)
    app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)

    @app.after_request
    def after_request(response: flask.Response) -> flask.Response:
        observations.append(("after_request", response.status_code))
        response.headers.add("X-Boundary", "first")
        response.headers.add("X-Boundary", "second")
        return response

    @app.teardown_request
    def teardown_request(error: Optional[BaseException]) -> None:
        observations.append(("teardown", type(error) if error else None))

    return app, observations


def call_wsgi(app: flask.Flask, observations: list[tuple[str, Any]], path: str, method: str = "GET") -> Any:
    builder = EnvironBuilder(path=path, method=method)

    def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
        observations.append(("start_response", (int(status.split()[0]), headers)))
        return lambda data: None

    try:
        # A test client can advance the iterator before returning, hiding this boundary.
        result = app.wsgi_app(builder.get_environ(), start_response)
        observations.append(("wsgi_return", None))
        return result
    finally:
        builder.close()


@pytest.mark.parametrize(
    "case,method,status,matched_route,dispatch_returns",
    [
        ("normal", "GET", 200, "/", True),
        ("short_circuit", "GET", 202, "/", True),
        ("missing", "GET", 404, None, True),
        ("method", "POST", 405, None, True),
        ("options", "OPTIONS", 200, "/", True),
        ("handled", "GET", 409, "/", True),
        ("unhandled", "GET", 500, "/", False),
    ],
)
def test_response_preparation_boundary(
    boundary_app: Any, case: str, method: str, status: int, matched_route: Optional[str], dispatch_returns: bool
) -> None:
    app, observations = boundary_app

    @app.route("/")
    def view() -> Any:
        observations.append(("view", None))
        if case == "handled":
            flask.abort(409)
        if case == "unhandled":
            raise ValueError("application failure")
        return "ok", 200

    @app.errorhandler(409)
    def handled_error(error: Any) -> Any:
        return "handled", 409

    @app.before_request
    def before_request() -> Any:
        if case == "short_circuit":
            return "early", 202
        return None

    result = call_wsgi(app, observations, "/missing" if case == "missing" else "/", method)
    try:
        kinds = [kind for kind, _ in observations]
        assert observations[0] == ("route", matched_route)
        assert observations.count(("prepared", (status, ["first", "second"]))) == 1
        assert kinds.index("after_request") < kinds.index("prepared") < kinds.index("start_response")
        assert kinds.index("start_response") < kinds.index("teardown") < kinds.index("wsgi_return")
        assert ("dispatch_return" in kinds) is dispatch_returns
        assert kinds.count("view") == int(case in ("normal", "handled", "unhandled"))
        assert kinds.count("after_request") == kinds.count("teardown") == 1
        wire_status, headers = next(value for kind, value in observations if kind == "start_response")
        assert wire_status == status
        assert [value for name, value in headers if name == "X-Boundary"] == ["first", "second"]
        assert not flask.has_request_context()
    finally:
        result.close()


@pytest.mark.parametrize("termination", ["exhaust", "early_close", "iteration_error"])
def test_wsgi_return_precedes_stream_completion(boundary_app: Any, termination: str) -> None:
    app, observations = boundary_app

    @app.route("/")
    def view() -> flask.Response:
        def body() -> Iterator[bytes]:
            try:
                observations.append(("yield", None))
                yield b"first"
                if termination == "iteration_error":
                    raise ValueError("stream failure")
                yield b"second"
            finally:
                observations.append(("body_closed", None))

        response = app.response_class(body())
        response.call_on_close(lambda: observations.append(("response_closed", None)))
        return response

    result = call_wsgi(app, observations, "/")
    try:
        assert observations[-1] == ("wsgi_return", None)
        assert not any(kind in ("yield", "body_closed", "response_closed") for kind, _ in observations)
        assert not flask.has_request_context()
        iterator = iter(result)
        assert next(iterator) == b"first"
        if termination == "iteration_error":
            with pytest.raises(ValueError, match="stream failure"):
                next(iterator)
        elif termination == "exhaust":
            assert list(iterator) == [b"second"]
        # Response close callbacks belong to the server's close, even after exhaustion.
        assert ("response_closed", None) not in observations
    finally:
        result.close()
    assert observations.count(("body_closed", None)) == 1
    assert observations.count(("response_closed", None)) == 1
    assert observations.count(("teardown", None)) == 1
    assert observations.count(("prepared", (200, ["first", "second"]))) == 1
