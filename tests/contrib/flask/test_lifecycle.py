import sys

import flask
from werkzeug.test import EnvironBuilder

from ddtrace import config
from ddtrace.contrib.internal.flask.patch import _FlaskWSGIMiddleware
from ddtrace.internal import core

from . import BaseFlaskTestCase


class FlaskLifecycleTestCase(BaseFlaskTestCase):
    def setUp(self):
        super().setUp()
        self.phases = []
        self.roots = []
        self.scopes = []

        def starting(ctx):
            self.phases.append("starting")
            self.assertIsNone(ctx.get_item("req_span"))
            ctx.event.tags["lifecycle.starting"] = "observed"

        def started(ctx):
            self.phases.append("started")
            self.roots.append(ctx.get_item("req_span"))
            self.scopes.append(ctx)
            self.assertEqual(self.roots[-1].get_tag("lifecycle.starting"), "observed")
            self.assertFalse(self.roots[-1].finished)

        def finishing(ctx):
            self.phases.append("finishing")
            self.assertFalse(ctx.get_item("req_span").finished)
            ctx.event.additional_tags["lifecycle.finishing"] = "observed"

        for phase, handler in (("starting", starting), ("started", started), ("finishing", finishing)):
            name = f"span.{phase}.flask.request"
            core.on(name, handler)
            self.addCleanup(core.reset_listeners, name, handler)

    def open_response(self, path="/", app=None):
        builder = EnvironBuilder(path=path)
        self.addCleanup(builder.close)
        response = (app or self.app)(builder.get_environ(), lambda status, headers, exc_info=None: lambda data: None)
        self.addCleanup(response.close)
        return response

    def test_stream_completion_and_context_restoration(self):
        chunks = []
        children = []

        @self.app.route("/stream")
        def stream():
            @flask.stream_with_context
            def generate():
                self.assertEqual(flask.request.path, "/stream")
                with self.tracer.trace("stream.child") as child:
                    children.append(child)
                    chunks.append(1)
                    yield b"first"
                yield b"second"

            return flask.Response(generate())

        with self.tracer.trace("caller") as caller:
            response = self.open_response("/stream")
            self.assertEqual(chunks, [])
            self.assertEqual(self.phases, ["starting", "started"])
            self.assertIs(self.tracer.current_span(), caller)
            self.assertEqual(next(response), b"first")
            response_span = self.scopes[0].get_item("resp_span")
            self.assertEqual(children[0].parent_id, response_span.span_id)
            self.assertIs(self.tracer.current_span(), caller)
            self.assertEqual(list(response), [b"second"])
            response.close()
            self.assertIs(self.tracer.current_span(), caller)
        self.assertEqual(self.phases, ["starting", "started", "finishing"])
        self.assertTrue(self.roots[0].finished)
        self.assertTrue(response_span.finished)
        self.assertEqual(self.roots[0].get_tag("lifecycle.finishing"), "observed")

    def test_close_before_first_chunk(self):
        closed = []

        @self.app.route("/")
        def index():
            response = flask.Response(iter([b"first", b"second"]))
            response.call_on_close(lambda: closed.append(True))
            return response

        response = self.open_response()
        response.close()
        response.close()
        self.assertEqual(closed, [True])
        self.assertTrue(self.roots[0].finished)
        self.assertEqual(self.phases.count("finishing"), 1)

    def test_stream_error_and_cleanup_error_preserve_original(self):
        stream_error = ValueError("stream failure")

        def close():
            raise RuntimeError("close failure")

        @self.app.route("/")
        def index():
            def generate():
                yield b"first"
                raise stream_error

            response = flask.Response(generate())
            response.call_on_close(close)
            return response

        response = self.open_response()
        self.assertEqual(next(response), b"first")
        with self.assertRaises(ValueError) as raised:
            next(response)
        self.assertIs(raised.exception, stream_error)
        response.close()
        self.assertEqual(self.roots[0].error, 0)
        self.assertTrue(self.roots[0].finished)
        self.assertEqual(self.scopes[0].get_item("resp_span").error, 1)
        self.assertEqual(self.phases.count("finishing"), 1)

    def test_close_error_finishes_response_and_request(self):
        def close():
            raise ValueError("close failure")

        @self.app.route("/")
        def index():
            response = flask.Response([b"first"])
            response.call_on_close(close)
            return response

        response = self.open_response()
        with self.assertRaisesRegex(ValueError, "close failure"):
            response.close()
        self.assertTrue(self.roots[0].finished)
        self.assertEqual(self.roots[0].error, 0)
        self.assertEqual(self.scopes[0].get_item("resp_span").error, 1)
        self.assertEqual(self.phases.count("finishing"), 1)

    def test_application_error_finishes_request(self):
        self.app.config["PROPAGATE_EXCEPTIONS"] = True

        @self.app.route("/")
        def index():
            raise ValueError("application failure")

        with self.assertRaisesRegex(ValueError, "application failure"):
            self.open_response()
        self.assertTrue(self.roots[0].finished)
        self.assertEqual(self.roots[0].error, 1)
        self.assertEqual(self.phases.count("finishing"), 1)

    def test_application_stop_iteration_is_not_an_error(self):
        def app(environ, start_response):
            raise StopIteration

        middleware = _FlaskWSGIMiddleware(app, None, config.flask)
        with self.assertRaises(StopIteration):
            self.open_response(app=middleware)
        self.assertTrue(self.roots[0].finished)
        self.assertEqual(self.roots[0].error, 0)
        self.assertEqual(self.phases.count("finishing"), 1)
        self.assertTrue(all(span.finished for span in self.get_spans()))

    def test_interleaved_requests_keep_independent_contexts(self):
        seen = []

        @self.app.route("/<name>")
        def index(name):
            def generate():
                with self.tracer.trace("stream.child") as child:
                    seen.append((name, child.parent_id))
                    yield name.encode()

            return flask.Response(generate())

        first = self.open_response("/first")
        second = self.open_response("/second")
        self.assertEqual(next(second), b"second")
        self.assertEqual(next(first), b"first")
        first.close()
        second.close()
        self.assertEqual(
            seen,
            [
                ("second", self.scopes[1].get_item("resp_span").span_id),
                ("first", self.scopes[0].get_item("resp_span").span_id),
            ],
        )
        self.assertEqual(self.phases.count("finishing"), 2)
        self.assertTrue(all(root.finished for root in self.roots))

    def test_start_response_preserves_exc_info_and_write(self):
        writes = []
        calls = []
        try:
            raise ValueError("original")
        except ValueError:
            exc_info = sys.exc_info()

        def start_response(status, headers, received_exc_info=None):
            calls.append(received_exc_info)
            return writes.append

        def app(environ, respond):
            with self.app.request_context(environ):
                write = respond("200 OK", [], exc_info)
                write(b"written")
            return [b"body"]

        builder = EnvironBuilder()
        self.addCleanup(builder.close)
        middleware = _FlaskWSGIMiddleware(app, None, config.flask)
        response = middleware(builder.get_environ(), start_response)
        self.addCleanup(response.close)
        self.assertEqual(list(response), [b"body"])
        self.assertEqual(calls, [exc_info])
        self.assertEqual(writes, [b"written"])

    def test_custom_resource_survives_response_completion(self):
        @self.app.route("/")
        def index():
            self.tracer.current_root_span().resource = "custom-view"
            return "ok"

        response = self.open_response()
        self.assertEqual(self.roots[0].resource, "custom-view")
        list(response)
        self.assertEqual(self.roots[0].resource, "custom-view")

    def test_streamed_custom_resource_survives_completion(self):
        @self.app.route("/")
        def index():
            def generate():
                self.tracer.current_root_span().resource = "custom-stream"
                yield b"ok"

            return flask.Response(generate())

        response = self.open_response()
        list(response)
        self.assertEqual(self.roots[0].resource, "custom-stream")

    def test_stream_with_context_can_close_early(self):
        closed = []

        @self.app.route("/")
        def index():
            @flask.stream_with_context
            def generate():
                try:
                    yield flask.request.path.encode()
                    yield b"unused"
                finally:
                    closed.append(flask.request.path)

            return flask.Response(generate())

        response = self.open_response()
        self.assertEqual(next(response), b"/")
        response.close()
        self.assertEqual(closed, ["/"])
        self.assertTrue(self.roots[0].finished)
        self.assertEqual(self.phases.count("finishing"), 1)
