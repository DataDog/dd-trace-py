from collections.abc import Callable
from collections.abc import Generator
from io import BytesIO
from io import StringIO
from itertools import cycle
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
    propagation_headers: bool

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
            # TracePlugin's constructor is currently untyped.
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
        environ.update(PATH_INFO="/benchmark", HTTP_HOST="localhost")
        request_environments = []
        for index in range(64):
            request_environ = dict(environ)
            if self.propagation_headers:
                request_environ.update(
                    HTTP_X_DATADOG_TRACE_ID=str(index + 1),
                    HTTP_X_DATADOG_PARENT_ID=str(index + 65),
                    HTTP_X_DATADOG_SAMPLING_PRIORITY="1",
                )
            request_environments.append(request_environ)
        # Rotate precomputed contexts without generating IDs in the timed loop.
        environments = cycle(request_environments)

        def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> None:
            pass

        def make_request() -> bytes:
            # Bottle mutates the WSGI environment and writes exception reports to wsgi.errors.
            request_environ = dict(next(environments), **{"wsgi.input": BytesIO(), "wsgi.errors": StringIO()})
            response = app(request_environ, start_response)
            try:
                return b"".join(response)
            finally:
                if hasattr(response, "close"):
                    response.close()

        # Prime Bottle's route and plugin caches before timing requests.
        make_request()

        def requests(loops: int) -> None:
            for _ in range(loops):
                make_request()

        yield requests
