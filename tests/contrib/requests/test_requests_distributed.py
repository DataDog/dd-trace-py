
# 3p
from nose.tools import eq_, assert_not_equal
from threading import Lock, Thread
from sys import version_info
if version_info[0] < 3:
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
else:
    from http.server import BaseHTTPRequestHandler, HTTPServer
from time import sleep

# project
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.ext import http, errors
from tests.test_tracer import get_dummy_tracer
from .test_requests import get_traced_session

# host/port on which our dumb server is listening
_SERVER_ADDRESS = ('localhost', 8082)

_lock = Lock()
_running = None
_client_tracer, _session = get_traced_session()
_server_tracer = get_dummy_tracer()
_httpd = None

class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        propagator = HTTPPropagator()
        context = propagator.extract(self.headers)
        if context.trace_id:
            _server_tracer.context_provider.activate(context)
        with _server_tracer.trace('handle_request', service='http_server'):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            content = "<html><body><h1>hello world</h1></body></html>"
            if version_info[0] < 3:
                self.wfile.write(content)
            else:
                self.wfile.write(bytes(content, "utf-8"))

def _serverThread():
    while _keep_running():
        _httpd.handle_request()
    _httpd.server_close()

def _keep_running():
    with _lock:
        return _running

def _run(value):
    global _running
    with _lock:
        _running = bool(value)

def _test_propagation(distributed_tracing_enabled=None):
    _session.distributed_tracing_enabled = distributed_tracing_enabled
    url = ('http://%s:%d/' % _SERVER_ADDRESS) + str(distributed_tracing_enabled)
    out = _session.get(url, timeout=3)
    eq_(out.status_code, 200)
    # client validation
    spans = _client_tracer.writer.pop()
    eq_(len(spans), 1)
    client_span = spans[0]
    eq_(client_span.get_tag(http.METHOD), 'GET')
    eq_(client_span.get_tag(http.STATUS_CODE), '200')
    eq_(client_span.error, 0)
    eq_(client_span.span_type, http.TYPE)
    # server validation
    spans = _server_tracer.writer.pop()
    eq_(len(spans), 1)
    server_span = spans[0]
    eq_(server_span.name, 'handle_request')
    eq_(server_span.service, 'http_server')
    eq_(server_span.error, 0)
    # propagation check
    if distributed_tracing_enabled:
        eq_(server_span.trace_id, client_span.trace_id)
        eq_(server_span.parent_id, client_span.span_id)
    else:
        assert_not_equal(server_span.trace_id, client_span.trace_id)
        assert_not_equal(server_span.parent_id, client_span.span_id)

class TestRequestsDistributed(object):

    def setUp(self):
        global _httpd
        _httpd = HTTPServer(_SERVER_ADDRESS, DummyServer)
        self.thread = Thread(target = _serverThread)
        _run(True)
        self.thread.start()

    def tearDown(self):
        global _httpd
        _run(False)
        # run a dumb request to trigger a call to _keep_running()
        url = 'http://%s:%d/quit' % _SERVER_ADDRESS
        _session.get(url, timeout=3)
        self.thread.join()
        self.thread = None
        _httpd = None
        _client_tracer.writer.pop()
        _server_tracer.writer.pop()

    @staticmethod
    def test_propagation_enabled():
        _test_propagation(True)

    @staticmethod
    def test_propagation_disabled():
        _test_propagation(False)

    @staticmethod
    def test_propagation_default():
        _test_propagation(None)
