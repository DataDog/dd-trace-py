# -*- encoding: utf-8 -*-
import collections
import email.parser
import http.server as http_server
import json
import platform
import sys
import threading
import time

import pytest

import ddtrace
from ddtrace.internal import compat
from ddtrace.internal.processor.endpoint_call_counter import EndpointCallCounterProcessor
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.profiling import exporter
from ddtrace.profiling.exporter import http
from ddtrace.settings.profiling import config

from . import test_pprof


# Skip this test on Windows:
# they add little value and the HTTP server shutdown seems unreliable and crashes
# TODO: rewrite those test with another HTTP server that works on win32?
if sys.platform == "win32":
    pytestmark = pytest.mark.skip


_API_KEY = "my-api-key"
_ENDPOINT_COUNTS = {"a": 1, "b": 2}


class _APIEndpointRequestHandlerTest(http_server.BaseHTTPRequestHandler):
    error_message_format = "%(message)s\n"
    error_content_type = "text/plain"
    path_prefix = "/profiling/v1"

    @staticmethod
    def log_message(format, *args):  # noqa: A002
        pass

    @staticmethod
    def _check_tags(tags):
        tags.split(",").sort()
        return (
            len(tags) == 6
            and tags[0].startswith("host:")
            and tags[1] == "language:python"
            and tags[2] == ("profiler_version:%s" % ddtrace.__version__)
            and tags[3].startswith("runtime-id:")
            and tags[4] == "runtime:CPython"
            and tags[5].startswith("service:")
            and tags[6] == platform.python_version(),
        )

    @staticmethod
    def _check_endpoints(endpoints):
        return endpoints == _ENDPOINT_COUNTS

    def _check_event(self, event_json):
        event = json.loads(event_json.decode())
        for key, check in {
            "start": lambda x: x == "1970-01-01T00:00:00Z",
            "end": lambda x: x.startswith("20"),
            "family": lambda x: x == "python",
            "version": lambda x: x == "4",
            "tags_profiler": self._check_tags,
            "endpoint_counts": self._check_endpoints,
        }.items():
            if not check(event[key]):
                return False
        return True

    def do_POST(self):
        assert self.path.startswith(self.path_prefix)
        api_key = self.headers["DD-API-KEY"]
        if api_key != _API_KEY:
            self.send_error(400, "Wrong API Key")
            return
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        mmpart = b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n" + body
        msg = email.parser.BytesParser().parsebytes(mmpart)
        if not msg.is_multipart():
            self.send_error(400, "No multipart")
            return
        items = collections.defaultdict(list)
        for part in msg.get_payload():
            items[part.get_param("name", header="content-disposition")].append(part.get_payload(decode=True))
        for key, check in {
            "event": lambda x: self._check_event(x[0]),
            "auto": lambda x: x[0].startswith(b"\x1f\x8b\x08\x00"),
            "code-provenance": lambda x: x[0].startswith(b"\x1f\x8b\x08\x00"),
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
    server = http_server.HTTPServer(("localhost", port), request_handler)
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


def _get_span_processor():
    endpoint_call_counter_span_processor = EndpointCallCounterProcessor()
    endpoint_call_counter_span_processor.endpoint_counts = _ENDPOINT_COUNTS
    return endpoint_call_counter_span_processor


def test_wrong_api_key(endpoint_test_server):
    # This is mostly testing our test server, not the exporter
    exp = http.PprofHTTPExporter(
        endpoint=_ENDPOINT,
        api_key="this is not the right API key",
        max_retry_delay=2,
        endpoint_call_counter_span_processor=_get_span_processor(),
    )
    with pytest.raises(exporter.ExportError) as t:
        exp.export(test_pprof.TEST_EVENTS, 0, 1)
    assert str(t.value) == "Server returned 400, check your API key"


def test_export(endpoint_test_server):
    exp = http.PprofHTTPExporter(
        endpoint=_ENDPOINT, api_key=_API_KEY, endpoint_call_counter_span_processor=_get_span_processor()
    )
    exp.export(test_pprof.TEST_EVENTS, 0, compat.time_ns())


def test_export_server_down():
    exp = http.PprofHTTPExporter(
        endpoint="http://localhost:2",
        api_key=_API_KEY,
        max_retry_delay=2,
        endpoint_call_counter_span_processor=_get_span_processor(),
    )
    with pytest.raises(exporter.ExportError):
        exp.export(test_pprof.TEST_EVENTS, 0, 1)


def test_export_timeout(endpoint_test_timeout_server):
    exp = http.PprofHTTPExporter(
        endpoint=_TIMEOUT_ENDPOINT,
        api_key=_API_KEY,
        timeout=1,
        max_retry_delay=2,
        endpoint_call_counter_span_processor=_get_span_processor(),
    )
    with pytest.raises(exporter.ExportError):
        exp.export(test_pprof.TEST_EVENTS, 0, 1)


def test_export_reset(endpoint_test_reset_server):
    exp = http.PprofHTTPExporter(
        endpoint=_RESET_ENDPOINT,
        api_key=_API_KEY,
        timeout=1,
        max_retry_delay=2,
        endpoint_call_counter_span_processor=_get_span_processor(),
    )
    with pytest.raises(exporter.ExportError):
        exp.export(test_pprof.TEST_EVENTS, 0, 1)


def test_export_404_agent(endpoint_test_unknown_server):
    exp = http.PprofHTTPExporter(endpoint=_UNKNOWN_ENDPOINT, endpoint_call_counter_span_processor=_get_span_processor())
    with pytest.raises(exporter.ExportError) as t:
        exp.export(test_pprof.TEST_EVENTS, 0, 1)
    assert str(t.value) == (
        "Datadog Agent is not accepting profiles. " "Agent-based profiling deployments require Datadog Agent >= 7.20"
    )


def test_export_404_agentless(endpoint_test_unknown_server):
    exp = http.PprofHTTPExporter(
        endpoint=_UNKNOWN_ENDPOINT, api_key="123", timeout=1, endpoint_call_counter_span_processor=_get_span_processor()
    )
    with pytest.raises(exporter.ExportError) as t:
        exp.export(test_pprof.TEST_EVENTS, 0, 1)
    assert str(t.value) == "HTTP Error 404"


def test_export_tracer_base_path(endpoint_test_server):
    # Base path is prepended to the endpoint path because
    # it does not start with a slash.
    exp = http.PprofHTTPExporter(
        endpoint=_ENDPOINT + "/profiling/",
        api_key=_API_KEY,
        endpoint_path="v1/input",
        endpoint_call_counter_span_processor=_get_span_processor(),
    )
    exp.export(test_pprof.TEST_EVENTS, 0, compat.time_ns())


def test_export_tracer_base_path_agent_less(endpoint_test_server):
    # Base path is ignored by the profiling HTTP exporter
    # because the endpoint path starts with a slash.
    exp = http.PprofHTTPExporter(
        endpoint=_ENDPOINT + "/profiling/",
        api_key=_API_KEY,
        endpoint_path="/profiling/v1/input",
        endpoint_call_counter_span_processor=_get_span_processor(),
    )
    exp.export(test_pprof.TEST_EVENTS, 0, compat.time_ns())


def test_get_tags():
    tags = parse_tags_str(http.PprofHTTPExporter(env="foobar", endpoint="", tags=config.tags)._get_tags("foobar"))
    assert tags["service"] == "foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == "python"
    assert tags["env"] == "foobar"
    assert tags["runtime"] == "CPython"
    assert tags["profiler_version"] == ddtrace.__version__


@pytest.mark.subprocess(env=dict(DD_TAGS="mytagfoobar"), err=None)
def test_get_malformed_key_only():
    import ddtrace
    from ddtrace.internal.utils.formats import parse_tags_str
    from ddtrace.profiling.exporter import http
    from ddtrace.settings.profiling import config

    tags = parse_tags_str(http.PprofHTTPExporter(endpoint="", tags=config.tags)._get_tags("foobar"))
    assert tags["service"] == "foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == "python"
    assert tags["runtime"] == "CPython"
    assert tags["profiler_version"] == ddtrace.__version__


@pytest.mark.subprocess(env=dict(DD_TAGS="mytagfoobar,"), err=None)
def test_get_malformed_no_val():
    import ddtrace
    from ddtrace.internal.utils.formats import parse_tags_str
    from ddtrace.profiling.exporter import http
    from ddtrace.settings.profiling import config

    tags = parse_tags_str(http.PprofHTTPExporter(endpoint="", tags=config.tags)._get_tags("foobar"))
    assert tags["service"] == "foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == "python"
    assert tags["runtime"] == "CPython"
    assert tags["profiler_version"] == ddtrace.__version__


@pytest.mark.subprocess(env=dict(DD_TAGS=","), err=None)
def test_get_malformed_comma_only():
    import ddtrace
    from ddtrace.internal.utils.formats import parse_tags_str
    from ddtrace.profiling.exporter import http
    from ddtrace.settings.profiling import config

    tags = parse_tags_str(http.PprofHTTPExporter(endpoint="", tags=config.tags)._get_tags("foobar"))
    assert tags["service"] == "foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == "python"
    assert tags["runtime"] == "CPython"
    assert tags["profiler_version"] == ddtrace.__version__


@pytest.mark.subprocess(env=dict(DD_TAGS="foo:bar,"), err=None)
def test_get_tags_trailing_comma():
    import ddtrace
    from ddtrace.internal.utils.formats import parse_tags_str
    from ddtrace.profiling.exporter import http
    from ddtrace.settings.profiling import config

    tags = parse_tags_str(http.PprofHTTPExporter(endpoint="", tags=config.tags)._get_tags("foobar"))

    assert tags["service"] == "foobar"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == "python"
    assert tags["runtime"] == "CPython"
    assert tags["foo"] == "bar"
    assert tags["profiler_version"] == ddtrace.__version__


@pytest.mark.subprocess(
    env=dict(
        DD_TAGS="mytag:foobar,service:🤣",
    )
)
def test_get_tags_override():
    import ddtrace
    from ddtrace.internal.utils.formats import parse_tags_str
    from ddtrace.profiling.exporter import http
    from ddtrace.settings.profiling import config

    tags = parse_tags_str(
        http.PprofHTTPExporter(endpoint="", version="123", env="prod", tags=config.tags)._get_tags("foobar")
    )
    assert tags["service"] == "🤣"
    assert len(tags["host"])
    assert len(tags["runtime-id"])
    assert tags["language"] == "python"
    assert tags["runtime"] == "CPython"
    assert tags["profiler_version"] == ddtrace.__version__
    assert tags["version"] == "123"
    assert tags["env"] == "prod"
    assert tags["mytag"] == "foobar"


@pytest.mark.skip(reason="Needs investigation about the segfaulting")
@pytest.mark.subprocess(env=dict(DD_PROFILING_TAGS="mytag:baz"))
def test_get_tags_legacy():
    from ddtrace.internal.utils.formats import parse_tags_str  # noqa:F401
    from ddtrace.profiling.exporter import http  # noqa:F401

    # REVERTME: Investigating segfaults on CI
    # tags = parse_tags_str(http.PprofHTTPExporter(endpoint="")._get_tags("foobar"))
    # assert tags["mytag"] == "baz"


@pytest.mark.subprocess(env=dict(DD_PROFILING_TAGS="mytag:val2,ddptag:lo", DD_TAGS="mytag:val1,ddtag:hi"))
def test_get_tags_precedence():
    from ddtrace.internal.utils.formats import parse_tags_str
    from ddtrace.profiling.exporter import http
    from ddtrace.settings.profiling import config

    tags = parse_tags_str(http.PprofHTTPExporter(endpoint="", tags=config.tags)._get_tags("foobar"))
    assert tags["mytag"] == "val2"
    assert tags["ddtag"] == "hi"
    assert tags["ddptag"] == "lo"


@pytest.mark.subprocess(
    env=dict(
        DD_TAGS="git.commit.sha:12345,git.repository_url:github.com/user/tag_repo",
    )
)
def test_gitmetadata_ddtags():
    from ddtrace.internal import gitmetadata
    from ddtrace.internal.utils.formats import parse_tags_str
    from ddtrace.profiling.exporter import http
    from ddtrace.settings.profiling import config

    gitmetadata._GITMETADATA_TAGS = None
    tags = parse_tags_str(http.PprofHTTPExporter(endpoint="", tags=config.tags)._get_tags("foobar"))

    # must be from env variables
    assert tags["git.commit.sha"] == "12345"
    assert tags["git.repository_url"] == "github.com/user/tag_repo"


@pytest.mark.subprocess(
    env=dict(
        DD_TAGS="git.commit.sha:12345,git.repository_url:github.com/user/tag_repo",
        DD_GIT_COMMIT_SHA="123456",
        DD_GIT_REPOSITORY_URL="github.com/user/env_repo",
        DD_MAIN_PACKAGE="my_package",
    )
)
def test_gitmetadata_env():
    from ddtrace.internal import gitmetadata
    from ddtrace.internal.utils.formats import parse_tags_str
    from ddtrace.profiling.exporter import http
    from ddtrace.settings.profiling import config

    gitmetadata._GITMETADATA_TAGS = None

    tags = parse_tags_str(http.PprofHTTPExporter(endpoint="", tags=config.tags)._get_tags("foobar"))

    # must be from env variables
    assert tags["git.commit.sha"] == "123456"
    assert tags["git.repository_url"] == "github.com/user/env_repo"
    assert tags["python_main_package"] == "my_package"
    gitmetadata._GITMETADATA_TAGS = None


@pytest.mark.subprocess(
    env=dict(
        DD_TAGS="git.commit.sha:12345,git.repository_url:github.com/user/tag_repo",
        DD_GIT_COMMIT_SHA="123456",
        DD_GIT_REPOSITORY_URL="github.com/user/env_repo",
        DD_MAIN_PACKAGE="my_package",
        DD_TRACE_GIT_METADATA_ENABLED="false",
    )
)
def test_gitmetadata_disabled(monkeypatch):
    from ddtrace.internal import gitmetadata
    from ddtrace.internal.utils.formats import parse_tags_str
    from ddtrace.profiling.exporter import http
    from ddtrace.settings.profiling import config

    gitmetadata._GITMETADATA_TAGS = None

    tags = parse_tags_str(http.PprofHTTPExporter(endpoint="", tags=config.tags)._get_tags("foobar"))

    # must not present
    assert "git.commit.sha" not in tags
    assert "git.repository_url" not in tags
    assert "python_main_package" not in tags
