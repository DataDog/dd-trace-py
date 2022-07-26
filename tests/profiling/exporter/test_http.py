# -*- encoding: utf-8 -*-
import collections
import email.parser
import platform
import socket
import sys
import threading
import time

import pytest
import six
from six.moves import BaseHTTPServer
from six.moves import http_client

import ddtrace
from ddtrace.internal import compat
from ddtrace.profiling import exporter
from ddtrace.profiling.exporter import http

from . import test_pprof


# Skip this test on Windows:
# they add little value and the HTTP server shutdown seems unreliable and crashes
# TODO: rewrite those test with another HTTP server that works on win32?
if sys.platform == "win32":
    pytestmark = pytest.mark.skip


_API_KEY = "my-api-key"


class _APIEndpointRequestHandlerTest(BaseHTTPServer.BaseHTTPRequestHandler):
    error_message_format = "%(message)s\n"
    error_content_type = "text/plain"
    path_prefix = "/profiling/v1"

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
        assert self.path.startswith(self.path_prefix)
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
            "start": lambda x: x[0] == b"1970-01-01T00:00:00Z",
            "end": lambda x: x[0].startswith(b"20"),
            "family": lambda x: x[0] == b"python",
            "version": lambda x: x[0] == b"3",
            "tags[]": self._check_tags,
            "data[auto.pprof]": lambda x: x[0].startswith(b"\x1f\x8b\x08\x00"),
            "data[code-provenance.json]": lambda x: x[0].startswith(b"\x1f\x8b\x08\x00"),
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


class _UnknownAPIEndpointRequestHandlerTest(_APIEndpointRequestHandlerTest):
    def do_POST(self):
        self.send_error(404, "Argh")


_PORT = 8992
_TIMEOUT_PORT = _PORT + 1
_RESET_PORT = _PORT + 2
_UNKNOWN_PORT = _PORT + 3
_ENDPOINT = "http://localhost:%d" % _PORT
_TIMEOUT_ENDPOINT = "http://localhost:%d" % _TIMEOUT_PORT
_RESET_ENDPOINT = "http://localhost:%d" % _RESET_PORT
_UNKNOWN_ENDPOINT = "http://localhost:%d" % _UNKNOWN_PORT


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


@pytest.fixture(scope="module")
def endpoint_test_unknown_server():
    server, thread = _make_server(_UNKNOWN_PORT, _UnknownAPIEndpointRequestHandlerTest)
    try:
        yield thread
    finally:
        server.shutdown()
        thread.join()


def test_wrong_api_key(endpoint_test_server):
    # This is mostly testing our test server, not the exporter
    exp = http.PprofHTTPExporter(endpoint=_ENDPOINT, api_key="this is not the right API key", max_retry_delay=2)
    with pytest.raises(exporter.ExportError) as t:
        exp.export(test_pprof.TEST_EVENTS, 0, 1)
    assert str(t.value) == "Server returned 400, check your API key"


def test_export(endpoint_test_server):
    exp = http.PprofHTTPExporter(endpoint=_ENDPOINT, api_key=_API_KEY)
    exp.export(test_pprof.TEST_EVENTS, 0, compat.time_ns())


def test_export_server_down():
    exp = http.PprofHTTPExporter(endpoint="http://localhost:2", api_key=_API_KEY, max_retry_delay=2)
    with pytest.raises(http.UploadFailed) as t:
        exp.export(test_pprof.TEST_EVENTS, 0, 1)
    e = t.value.last_attempt.exception()
    assert isinstance(e, (IOError, OSError))
    assert str(t.value).startswith("[Errno ") or str(t.value).startswith("[WinError ")


def test_export_timeout(endpoint_test_timeout_server):
    exp = http.PprofHTTPExporter(endpoint=_TIMEOUT_ENDPOINT, api_key=_API_KEY, timeout=1, max_retry_delay=2)
    with pytest.raises(http.UploadFailed) as t:
        exp.export(test_pprof.TEST_EVENTS, 0, 1)
    e = t.value.last_attempt.exception()
    assert isinstance(e, socket.timeout)
    assert str(t.value) == "timed out"


def test_export_reset(endpoint_test_reset_server):
    exp = http.PprofHTTPExporter(endpoint=_RESET_ENDPOINT, api_key=_API_KEY, timeout=1, max_retry_delay=2)
    with pytest.raises(http.UploadFailed) as t:
        exp.export(test_pprof.TEST_EVENTS, 0, 1)
    e = t.value.last_attempt.exception()
    if six.PY3:
        assert isinstance(e, ConnectionResetError)
    else:
        assert isinstance(e, http_client.BadStatusLine)
        assert str(e) == "No status line received - the server has closed the connection"


def test_export_404_agent(endpoint_test_unknown_server):
    exp = http.PprofHTTPExporter(endpoint=_UNKNOWN_ENDPOINT)
    with pytest.raises(exporter.ExportError) as t:
        exp.export(test_pprof.TEST_EVENTS, 0, 1)
    assert str(t.value) == (
        "Datadog Agent is not accepting profiles. " "Agent-based profiling deployments require Datadog Agent >= 7.20"
    )


def test_export_404_agentless(endpoint_test_unknown_server):
    exp = http.PprofHTTPExporter(endpoint=_UNKNOWN_ENDPOINT, api_key="123", timeout=1)
    with pytest.raises(exporter.ExportError) as t:
        exp.export(test_pprof.TEST_EVENTS, 0, 1)
    assert str(t.value) == "HTTP Error 404"


def test_export_tracer_base_path(endpoint_test_server):
    # Base path is prepended to the endpoint path because
    # it does not start with a slash.
    exp = http.PprofHTTPExporter(endpoint=_ENDPOINT + "/profiling/", api_key=_API_KEY, endpoint_path="v1/input")
    exp.export(test_pprof.TEST_EVENTS, 0, compat.time_ns())


def test_export_tracer_base_path_agent_less(endpoint_test_server):
    # Base path is ignored by the profiling HTTP exporter
    # because the endpoint path starts with a slash.
    exp = http.PprofHTTPExporter(
        endpoint=_ENDPOINT + "/profiling/", api_key=_API_KEY, endpoint_path="/profiling/v1/input"
    )
    exp.export(test_pprof.TEST_EVENTS, 0, compat.time_ns())


def _check_tags_types(tags):
    for k, v in tags.items():
        assert isinstance(k, str)
        assert isinstance(v, bytes)


def test_get_tags():
    tags = http.PprofHTTPExporter(env="foobar", endpoint="")._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 8
    assert tags["service"] == b"foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["env"] == b"foobar"
    assert tags["runtime"] == b"CPython"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")
    assert "version" not in tags


def test_get_malformed(monkeypatch):
    monkeypatch.setenv("DD_TAGS", "mytagfoobar")
    tags = http.PprofHTTPExporter(endpoint="")._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 7
    assert tags["service"] == b"foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")

    monkeypatch.setenv("DD_TAGS", "mytagfoobar,")
    tags = http.PprofHTTPExporter(endpoint="")._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 7
    assert tags["service"] == b"foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")

    monkeypatch.setenv("DD_TAGS", ",")
    tags = http.PprofHTTPExporter(endpoint="")._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 7
    assert tags["service"] == b"foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")

    monkeypatch.setenv("DD_TAGS", "foo:bar,")
    tags = http.PprofHTTPExporter(endpoint="")._get_tags("foobar")
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
    tags = http.PprofHTTPExporter(endpoint="")._get_tags("foobar")
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
    tags = http.PprofHTTPExporter(endpoint="")._get_tags("foobar")
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
    tags = http.PprofHTTPExporter(endpoint="")._get_tags("foobar")
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
    tags = http.PprofHTTPExporter(endpoint="")._get_tags("foobar")
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
    tags = http.PprofHTTPExporter(endpoint="")._get_tags("foobar")
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

    tags = http.PprofHTTPExporter(endpoint="", version="123")._get_tags("foobar")
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

    tags = http.PprofHTTPExporter(endpoint="", version="123", env="prod")._get_tags("foobar")
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

    tags = http.PprofHTTPExporter(endpoint="", version="123", env="prod", tags={"mytag": "123"})._get_tags("foobar")
    _check_tags_types(tags)
    assert len(tags) == 11
    assert tags["service"] == u"🤣".encode("utf-8")
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == b"python"
    assert tags["runtime"] == b"CPython"
    assert tags["foobar"] == b"baz"
    assert tags["profiler_version"] == ddtrace.__version__.encode("utf-8")
    assert tags["version"] == b"123"
    assert tags["env"] == b"prod"
    assert tags["mytag"] == b"123"


def test_get_tags_legacy(monkeypatch):
    monkeypatch.setenv("DD_PROFILING_TAGS", "mytag:baz")
    tags = http.PprofHTTPExporter(endpoint="")._get_tags("foobar")
    _check_tags_types(tags)
    assert tags["mytag"] == b"baz"

    # precedence
    monkeypatch.setenv("DD_TAGS", "mytag:val1,ddtag:hi")
    monkeypatch.setenv("DD_PROFILING_TAGS", "mytag:val2,ddptag:lo")
    tags = http.PprofHTTPExporter(endpoint="")._get_tags("foobar")
    _check_tags_types(tags)
    assert tags["mytag"] == b"val2"
    assert tags["ddtag"] == b"hi"
    assert tags["ddptag"] == b"lo"
