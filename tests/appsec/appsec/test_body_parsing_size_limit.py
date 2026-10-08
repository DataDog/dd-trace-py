"""Regression tests for DD_APPSEC_BODY_PARSING_SIZE_LIMIT (SEC-002).

The request-body collection in AppSec must never buffer more than the configured
limit, and an over-limit body must leave the application's stream untouched so
the app still observes its complete body.
"""

import asyncio

from ddtrace.appsec._contrib.fastapi import _on_asgi_request_parse_body
from ddtrace.appsec._contrib.flask import _ChainedInputStream
from ddtrace.appsec._contrib.flask import _on_request_span_modifier
from ddtrace.internal.settings.asm import config as asm_config
from tests.utils import override_global_config


ASM_ON = dict(_asm_enabled=True)


class _FakeWsgiInput:
    """Non-seekable WSGI input stream that tracks reads."""

    def __init__(self, data, seekable=False):
        self._data = data
        self._pos = 0
        self._seekable = seekable
        self.read_calls = 0

    def seekable(self):
        return self._seekable

    def read(self, size=-1):
        self.read_calls += 1
        if size is None or size < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + size]
        self._pos += size
        return chunk

    @property
    def remaining(self):
        return self._data[self._pos :]


class _FakeFlaskRequest:
    method = "POST"
    content_type = "application/json"

    def __init__(self, environ):
        self.environ = environ
        self._data = None

    @property
    def data(self):
        # mimic werkzeug: request.data caches the raw body after the first read
        if self._data is None:
            self._data = self.environ["wsgi.input"].read()
        return self._data


def _flask_call(environ, request=None):
    return _on_request_span_modifier(
        None,  # ctx
        None,  # flask config
        request or _FakeFlaskRequest(environ),
        environ,
        False,  # HAS_JSON_MIXIN
        None,
        "",
        Exception,
    )


def _wsgi_environ(stream, content_length=None, input_terminated=False):
    environ = {"wsgi.input": stream, "REQUEST_METHOD": "POST"}
    if content_length is not None:
        environ["CONTENT_LENGTH"] = str(content_length)
    if input_terminated:
        environ["wsgi.input_terminated"] = True
    return environ


# --------------------------------------------------------------------------
# Flask / WSGI
# --------------------------------------------------------------------------


def test_flask_body_within_limit_is_collected():
    body = b'{"value": "secret"}'
    stream = _FakeWsgiInput(body)
    environ = _wsgi_environ(stream, content_length=len(body))
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=1024)):
        req_body = _flask_call(environ)
    assert req_body == {"value": "secret"}
    # the app-visible stream is reset to the beginning and still serves the body
    assert environ["wsgi.input"].read() == body


def test_flask_body_over_limit_is_not_read_and_stream_is_untouched():
    body = b'{"value": "' + b"A" * 256 + b'"}'
    stream = _FakeWsgiInput(body)
    environ = _wsgi_environ(stream, content_length=len(body))
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=64)):
        req_body = _flask_call(environ)
    assert req_body is None
    assert stream.read_calls == 0
    assert environ["wsgi.input"] is stream
    assert stream.remaining == body  # the app still has the full body available


def test_flask_seekable_body_over_limit_is_not_parsed():
    body = b'{"value": "' + b"A" * 256 + b'"}'
    stream = _FakeWsgiInput(body, seekable=True)
    environ = _wsgi_environ(stream, content_length=len(body))
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=64)):
        req_body = _flask_call(environ)
    assert req_body is None
    # no framework-side buffering happened either: the parse was skipped entirely
    assert stream.read_calls == 0


def test_flask_input_terminated_body_over_limit_restores_full_body():
    body = b'{"value": "' + b"A" * 256 + b'"}'
    stream = _FakeWsgiInput(body)
    environ = _wsgi_environ(stream, input_terminated=True)
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=64)):
        req_body = _flask_call(environ)
    assert req_body is None
    restored = environ["wsgi.input"]
    assert isinstance(restored, _ChainedInputStream)
    # the application still observes the complete, ordered body
    assert restored.read() == body


def test_flask_input_terminated_body_within_limit_is_collected():
    body = b'{"value": 1}'
    stream = _FakeWsgiInput(body)
    environ = _wsgi_environ(stream, input_terminated=True)
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=1024)):
        req_body = _flask_call(environ)
    assert req_body == {"value": 1}
    assert environ["wsgi.input"].read() == body


def test_flask_unknown_content_length_is_not_collected():
    body = b'{"value": 1}'
    stream = _FakeWsgiInput(body)
    environ = _wsgi_environ(stream)  # no CONTENT_LENGTH, not input_terminated
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=1024)):
        req_body = _flask_call(environ)
    assert req_body is None
    assert stream.read_calls == 0
    assert environ["wsgi.input"] is stream


def test_flask_limit_zero_disables_collection():
    body = b'{"value": 1}'
    stream = _FakeWsgiInput(body)
    environ = _wsgi_environ(stream, content_length=len(body))
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=0)):
        req_body = _flask_call(environ)
    assert req_body is None
    assert stream.read_calls == 0
    assert environ["wsgi.input"] is stream


# --------------------------------------------------------------------------
# _ChainedInputStream
# --------------------------------------------------------------------------


def test_chained_stream_read_and_readline_across_boundary():
    prefix = b"line-one\npre"
    tail = _FakeWsgiInput(b"fix\nline-three\n")
    chained = _ChainedInputStream(prefix, tail)
    assert chained.readline() == b"line-one\n"
    assert chained.readline() == b"prefix\n"
    assert chained.readline() == b"line-three\n"
    assert chained.readline() == b""
    # read(n) across the boundary after a readline
    chained = _ChainedInputStream(b"01234", _FakeWsgiInput(b"56789"))
    assert chained.read(4) == b"0123"
    assert chained.read(4) == b"4567"  # 1 from prefix + 3 from tail
    assert chained.read() == b"89"
    assert chained.read() == b""


def test_chained_stream_readlines_and_iteration():
    chained = _ChainedInputStream(b"a\nb", _FakeWsgiInput(b"\nc\nd"))
    assert chained.readlines() == [b"a\n", b"b\n", b"c\n", b"d"]
    chained = _ChainedInputStream(b"a\n", _FakeWsgiInput(b"b\n"))
    assert list(chained) == [b"a\n", b"b\n"]
    chained = _ChainedInputStream(b"x", _FakeWsgiInput(b""))  # no trailing newline
    assert chained.readline() == b"x"


def test_chained_stream_full_read_is_bounded_then_complete():
    big_tail = b"B" * 100
    chained = _ChainedInputStream(b"A" * 10, _FakeWsgiInput(big_tail))
    assert chained.read() == b"A" * 10 + big_tail


# --------------------------------------------------------------------------
# ASGI / FastAPI
# --------------------------------------------------------------------------


def _asgi_receive(chunks):
    messages = [
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1} for i, chunk in enumerate(chunks)
    ]
    iterator = iter(messages)

    async def receive():
        try:
            return next(iterator)
        except StopIteration:
            return {"type": "http.disconnect"}

    return receive


def _run(coro):
    return asyncio.run(coro)


def test_asgi_body_within_limit_is_collected():
    receive = _asgi_receive([b'{"value": ', b"1}"])
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=1024)):
        receive_wrapped, req_body = _run(_on_asgi_request_parse_body(receive, {"content-type": "application/json"}))
    assert req_body == {"value": 1}
    first = _run(receive_wrapped())
    assert first == {"type": "http.request", "body": b'{"value": 1}', "more_body": False}


def test_asgi_body_over_limit_replays_and_skips_analysis():
    receive = _asgi_receive([b"A" * 4, b"B" * 4, b"C" * 4, b"D" * 4])
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=8)):
        receive_wrapped, req_body = _run(_on_asgi_request_parse_body(receive, {"content-type": "application/json"}))
    assert req_body is None
    # every consumed chunk is replayed to the application, in order, then the
    # remaining body keeps flowing live through the original receive
    results = [_run(receive_wrapped()) for _ in range(4)]
    assert results == [
        {"type": "http.request", "body": b"A" * 4, "more_body": True},
        {"type": "http.request", "body": b"B" * 4, "more_body": True},
        {"type": "http.request", "body": b"C" * 4, "more_body": True},
        {"type": "http.request", "body": b"D" * 4, "more_body": False},
    ]


def test_asgi_content_length_over_limit_leaves_receive_untouched():
    receive = _asgi_receive([b"A" * 100])
    headers = {"content-type": "application/json", "content-length": "100"}
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=8)):
        receive_wrapped, req_body = _run(_on_asgi_request_parse_body(receive, headers))
    assert req_body is None
    assert receive_wrapped is receive


def test_asgi_limit_zero_disables_collection():
    receive = _asgi_receive([b'{"value": 1}'])
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=0)):
        receive_wrapped, req_body = _run(_on_asgi_request_parse_body(receive, {"content-type": "application/json"}))
    assert req_body is None
    assert receive_wrapped is receive


# --------------------------------------------------------------------------
# Tornado
# --------------------------------------------------------------------------


class _TornadoRequest:
    def __init__(self, content_length, body=b""):
        self.headers = {"Content-Length": str(content_length)} if content_length is not None else {}
        self.body = body
        self.body_arguments = {}
        self.parse_body_called = False

    def _parse_body(self):
        self.parse_body_called = True
        self.body_arguments = {"collected": [b"yes"]}


class _TornadoHandler:
    def __init__(self, request):
        self.request = request


def _tornado_call(handler):
    from ddtrace.appsec._contrib.tornado import tornado_call_waf_first

    return tornado_call_waf_first("tornado", handler)


def test_tornado_body_over_limit_skips_parse_body():
    handler = _TornadoHandler(_TornadoRequest(content_length=1000000, body=b"A" * 1000000))
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=1024)):
        _tornado_call(handler)
    assert handler.request.parse_body_called is False


def test_tornado_body_within_limit_parses():
    handler = _TornadoHandler(_TornadoRequest(content_length=9, body=b'{"value": 1}'))
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=1024)):
        _tornado_call(handler)
    assert handler.request.parse_body_called is True


def test_tornado_missing_content_length_uses_body_size():
    # no Content-Length header: tornado already buffered the body, so its size
    # decides without reading anything new
    over = _TornadoHandler(_TornadoRequest(content_length=None, body=b"A" * 4096))
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=1024)):
        _tornado_call(over)
    assert over.request.parse_body_called is False

    under = _TornadoHandler(_TornadoRequest(content_length=None, body=b"A" * 512))
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=1024)):
        _tornado_call(under)
    assert under.request.parse_body_called is True


def test_tornado_limit_zero_disables_collection():
    handler = _TornadoHandler(_TornadoRequest(content_length=9, body=b'{"value": 1}'))
    with override_global_config(dict(ASM_ON, _asm_body_parsing_size_limit=0)):
        _tornado_call(handler)
    assert handler.request.parse_body_called is False


# --------------------------------------------------------------------------
# Setting
# --------------------------------------------------------------------------


def test_body_parsing_size_limit_default():
    assert asm_config._asm_body_parsing_size_limit == 10 * 1024 * 1024
