from collections.abc import Callable
from collections.abc import Generator
from io import BytesIO
from io import StringIO
import os
from typing import Any
from wsgiref.util import setup_testing_defaults

import bm


# The benchmark image provides bm; it is outside the repository's mypy import path.
class BottleRequest(bm.Scenario):  # type: ignore[misc]
    instrumented: bool
    tracer_enabled: bool
    outcome: str
    otel_semantics: bool

    def run(self) -> Generator[Callable[[int], None], None, None]:
        # Keep product workers and transport I/O out of request timing on both revisions.
        os.environ["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "false"
        os.environ["DD_REMOTE_CONFIGURATION_ENABLED"] = "false"
        os.environ["DD_TRACE_OTEL_SEMANTICS_ENABLED"] = str(self.otel_semantics).lower()

        from bm import utils
        import bottle

        from ddtrace.contrib.internal.bottle.patch import TracePlugin
        from ddtrace.trace import tracer

        utils.drop_telemetry_events()
        utils.drop_traces(tracer)
        tracer.enabled = self.tracer_enabled

        app = bottle.Bottle()
        if self.instrumented:
            # TracePlugin's constructor is untyped on the baseline revision.
            app.install(TracePlugin())  # type: ignore[no-untyped-call]

        def callback() -> Any:
            if self.outcome == "returned-response":
                return bottle.HTTPResponse("ok", status=201)
            if self.outcome == "raised-response":
                raise bottle.HTTPResponse("not found", status=404)
            if self.outcome == "exception":
                raise ValueError("benchmark callback failed")
            return "ok"

        app.route("/benchmark", callback=callback)

        environ: dict[str, Any] = {}
        setup_testing_defaults(environ)
        environ.update(
            PATH_INFO="/benchmark",
            HTTP_HOST="localhost",
            HTTP_X_DATADOG_TRACE_ID="123",
            HTTP_X_DATADOG_PARENT_ID="456",
            HTTP_X_DATADOG_SAMPLING_PRIORITY="1",
        )
        expected_status = {"returned-response": 201, "raised-response": 404, "exception": 500}.get(self.outcome, 200)

        def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> None:
            pass

        def make_request(responder: Callable[..., Any] = start_response) -> bytes:
            # Bottle mutates the WSGI environment and writes exception reports to wsgi.errors.
            request_environ = dict(environ, **{"wsgi.input": BytesIO(), "wsgi.errors": StringIO()})
            response = app(request_environ, responder)
            try:
                return b"".join(response)
            finally:
                if hasattr(response, "close"):
                    response.close()

        # Validate a real request and emitted span before entering the timed region.
        # Assertions check benchmark integrity, rather than enforcing security boundaries.
        captured: list[Any] = []
        writer = tracer._span_aggregator.writer
        setattr(writer, "write", captured.extend)
        statuses: list[int] = []

        def capture_status(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> None:
            statuses.append(int(status.split()[0]))

        body = make_request(capture_status)
        assert statuses == [expected_status], statuses  # nosec B101
        if self.outcome != "exception":
            assert body == (b"not found" if self.outcome == "raised-response" else b"ok")  # nosec B101
        if self.instrumented and self.tracer_enabled:
            (span,) = captured
            assert span.finished  # nosec B101
            assert span.name == "bottle.request"  # nosec B101
            assert span.resource == "GET /benchmark"  # nosec B101
            assert span.trace_id == 123 and span.parent_id == 456  # nosec B101
            assert span.error == int(  # nosec B101
                self.outcome == "exception" or (self.outcome == "raised-response" and not self.otel_semantics)
            )
            if self.outcome == "exception":
                assert span.get_tag("error.message") == "benchmark callback failed"  # nosec B101
        else:
            assert captured == []  # nosec B101
        assert tracer.current_span() is None  # nosec B101
        utils.drop_traces(tracer)

        def requests(loops: int) -> None:
            for _ in range(loops):
                make_request()

        yield requests
