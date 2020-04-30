# -*- encoding: utf-8 -*-
import collections
import errno
import email.parser
import platform
import socket
import threading
import time

import pytest

from ddtrace import compat
from ddtrace.vendor import six
from ddtrace.vendor.six.moves import BaseHTTPServer
from ddtrace.vendor.six.moves import http_client

import ddtrace
from ddtrace.profiling.exporter import http

from . import test_pprof


_API_KEY = "my-api-key"


class _APIEndpointRequestHandlerTest(BaseHTTPServer.BaseHTTPRequestHandler):
    error_message_format = "%(message)s\n"
    error_content_type = "text/plain"

    @staticmethod
    def log_message(format, *args):  # noqa: A002
        pass

    @staticmethod
    def _check_tags(tags):
        tags.sort()
        return (
            len(tags) == 6
            and tags[0].startswith(b"host:")
            and tags[1] == b"language:python"
            and tags[2] == ("profiler_version:%s" % ddtrace.__version__).encode("utf-8")
            and tags[3].startswith(b"runtime-id:")
            and tags[4] == b"runtime:CPython"
            and tags[5].startswith(b"service:")
            and tags[6] == platform.python_version().encode(),
        )

    def do_POST(self):
        api_key = self.headers["DD-API-KEY"]
        if api_key != _API_KEY:
            self.send_error(400, "Wrong API Key")
            return
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        mmpart = b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n" + body
        if six.PY2:
            msg = email.parser.Parser().parsestr(mmpart)
        else:
            msg = email.parser.BytesParser().parsebytes(mmpart)
        if not msg.is_multipart():
            self.send_error(400, "No multipart")
            return
        items = collections.defaultdict(list)
        for part in msg.get_payload():
            items[part.get_param("name", header="content-disposition")].append(part.get_payload(decode=True))
        for key, check in {
            "recording-start": lambda x: x[0] == b"1970-01-01T00:00:00Z",
            "recording-end": lambda x: x[0].startswith(b"20"),
            "runtime": lambda x: x[0] == platform.python_implementation().encode(),
            "format": lambda x: x[0] == b"pprof",
            "type": lambda x: x[0] == b"cpu+alloc+exceptions",
            "tags[]": self._check_tags,
            "chunk-data": lambda x: x[0].startswith(b"\x1f\x8b\x08\x00"),
        }.items():
            if not check(items[key]):
                self.send_error(400, "Wrong value for %s: %r" % (key, items[key]))
                return
        self.send_error(200, "OK")


class _TimeoutAPIEndpointRequestHandlerTest(_APIEndpointRequestHandlerTest):
    def do_POST(self):
        # This server sleeps longer than our timeout
        time.sleep(5)
        self.send_error(500, "Argh")


class _ResetAPIEndpointRequestHandlerTest(_APIEndpointRequestHandlerTest):
    def do_POST(self):
        return


_PORT = 8992
_TIMEOUT_PORT = _PORT + 1
_RESET_PORT = _PORT + 2
_ENDPOINT = "http://localhost:%d" % _PORT
_TIMEOUT_ENDPOINT = "http://localhost:%d" % _TIMEOUT_PORT
_RESET_ENDPOINT = "http://localhost:%d" % _RESET_PORT


def _make_server(port, request_handler):
    server = BaseHTTPServer.HTTPServer(("localhost", port), request_handler)
    t = threading.Thread(target=server.serve_forever)
    # Set daemon just in case something fails
    t.daemon = True
    t.start()
    return server, t


@pytest.fixture(scope="module")
def endpoint_test_server():
    server, thread = _make_server(_PORT, _APIEndpointRequestHandlerTest)
    try:
        yield thread
    finally:
        server.shutdown()
        thread.join()


@pytest.fixture(scope="module")
def endpoint_test_timeout_server():
    server, thread = _make_server(_TIMEOUT_PORT, _TimeoutAPIEndpointRequestHandlerTest)
    try:
        yield thread
    finally:
        server.shutdown()
        thread.join()


@pytest.fixture(scope="module")
def endpoint_test_reset_server():
    server, thread = _make_server(_RESET_PORT, _ResetAPIEndpointRequestHandlerTest)
    try:
        yield thread
    finally:
        server.shutdown()
        thread.join()


def test_wrong_api_key(endpoint_test_server):
    # This is mostly testing our test server, not the exporter
    exp = http.PprofHTTPExporter(_ENDPOINT, "this is not the right API key", max_retry_delay=10)
    with pytest.raises(http.UploadFailed) as t:
        exp.export(test_pprof.TEST_EVENTS, 0, 1)
        e = t.exception
        assert isinstance(e, http.RequestFailed)
        assert e.response.status == 400
        assert e.content == b"Wrong API Key\n"


def test_export(endpoint_test_server):
    exp = http.PprofHTTPExporter(_ENDPOINT, _API_KEY)
    exp.export(test_pprof.TEST_EVENTS, 0, compat.time_ns())


def test_export_no_endpoint(endpoint_test_server):
    exp = http.PprofHTTPExporter(endpoint="")
    with pytest.raises(http.InvalidEndpoint):
        exp.export(test_pprof.TEST_EVENTS, 0, 1)


def test_export_server_down():
    exp = http.PprofHTTPExporter("http://localhost:2", _API_KEY, max_retry_delay=10)
    with pytest.raises(http.UploadFailed) as t:
        exp.export(test_pprof.TEST_EVENTS, 0, 1)
        e = t.exception
        assert isinstance(e, (IOError, OSError))
        assert e.errno == errno.ECONNREFUSED


def test_export_timeout(endpoint_test_timeout_server):
    exp = http.PprofHTTPExporter(_TIMEOUT_ENDPOINT, _API_KEY, timeout=1, max_retry_delay=10)
    with pytest.raises(http.UploadFailed) as t:
        exp.export(test_pprof.TEST_EVENTS, 0, 1)
    e = t.value.exception
    assert isinstance(e, socket.timeout)


def test_export_reset(endpoint_test_reset_server):
    exp = http.PprofHTTPExporter(_RESET_ENDPOINT, _API_KEY, timeout=1)
    with pytest.raises(http.UploadFailed) as t:
        exp.export(test_pprof.TEST_EVENTS, 0, 1)
    e = t.value.exception
    if six.PY3:
        assert isinstance(e, ConnectionResetError)
    else:
        assert isinstance(e, http_client.BadStatusLine)


def test_default_from_env(monkeypatch):
    monkeypatch.setenv("DD_PROFILING_API_KEY", "123")
    exp = http.PprofHTTPExporter()
    assert exp.api_key == "123"
    assert exp.endpoint == "https://intake.profile.datadoghq.com/v1/input"

    monkeypatch.setenv("DD_PROFILING_API_URL", "foobar")
    exp = http.PprofHTTPExporter()
    assert exp.endpoint == "foobar"

    monkeypatch.setenv("DD_SITE", "datadoghq.eu")
    exp = http.PprofHTTPExporter()
    assert exp.endpoint == "foobar"

    monkeypatch.delenv("DD_PROFILING_API_URL")
    exp = http.PprofHTTPExporter()
    assert exp.endpoint == "https://intake.profile.datadoghq.eu/v1/input"

    monkeypatch.setenv("DD_API_KEY", "456")
    exp = http.PprofHTTPExporter()
    assert exp.api_key == "123"

    monkeypatch.delenv("DD_PROFILING_API_KEY")
    exp = http.PprofHTTPExporter()
    assert exp.api_key == "456"

    monkeypatch.setenv("DD_SERVICE", "myservice")
    exp = http.PprofHTTPExporter()
    assert exp.service_name == "myservice"


def _check_tags_types(tags):
    for k, v in tags.items():
        assert isinstance(k, str)
        assert isinstance(v, bytes)


def test_get_tags():
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 7
    assert tags["service"] == b"foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")
    assert "version" not in tags


def test_get_malformed(monkeypatch):
    monkeypatch.setenv("DD_TAGS", "mytagfoobar")
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 7
    assert tags["service"] == b"foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")

    monkeypatch.setenv("DD_TAGS", "mytagfoobar,")
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 7
    assert tags["service"] == b"foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")

    monkeypatch.setenv("DD_TAGS", ",")
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 7
    assert tags["service"] == b"foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")

    monkeypatch.setenv("DD_TAGS", "foo:bar,")
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 8
    assert tags["service"] == b"foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["foo"] == b"bar"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")


def test_get_tags_override(monkeypatch):
    monkeypatch.setenv("DD_TAGS", "mytag:foobar")
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 8
    assert tags["service"] == b"foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["mytag"] == b"foobar"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")
    assert "version" not in tags

    monkeypatch.setenv("DD_TAGS", "mytag:foobar,author:jd")
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 9
    assert tags["service"] == b"foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["mytag"] == b"foobar"
    assert tags["author"] == b"jd"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")
    assert "version" not in tags

    monkeypatch.setenv("DD_TAGS", "")
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 7
    assert tags["service"] == b"foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")
    assert "version" not in tags

    monkeypatch.setenv("DD_TAGS", "foobar:baz,service:mycustomservice")
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 8
    assert tags["service"] == b"mycustomservice"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["foobar"] == b"baz"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")
    assert "version" not in tags

    monkeypatch.setenv("DD_TAGS", "foobar:baz,service:🤣")
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 8
    assert tags["service"] == u"🤣".encode("utf-8")
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["foobar"] == b"baz"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")
    assert "version" not in tags

    monkeypatch.setenv("DD_VERSION", "123")
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 9
    assert tags["service"] == u"🤣".encode("utf-8")
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["foobar"] == b"baz"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")
    assert tags["version"] == b"123"
    assert "env" not in tags

    monkeypatch.setenv("DD_ENV", "prod")
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 10
    assert tags["service"] == u"🤣".encode("utf-8")
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["foobar"] == b"baz"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")
    assert tags["version"] == b"123"
    assert tags["env"] == b"prod"


def test_get_tags_legacy(monkeypatch):
    monkeypatch.setenv("DD_PROFILING_TAGS", "mytag:baz")
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert tags["mytag"] == b"baz"

    # precedence
    monkeypatch.setenv("DD_TAGS", "mytag:val1,ddtag:hi")
    monkeypatch.setenv("DD_PROFILING_TAGS", "mytag:val2,ddptag:lo")
    tags = http.PprofHTTPExporter()._get_tags("foobar")
    _check_tags_types(tags)
    assert tags["mytag"] == b"val2"
    assert tags["ddtag"] == b"hi"
    assert tags["ddptag"] == b"lo"
