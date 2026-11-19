import ddtrace.auto
from ddtrace.ext import SpanTypes  # noqa: F401


# ensure the tracer is loaded and started first for possible iast patching
print(f"ddtrace version {ddtrace.version.__version__}")

try:
    from ddtrace.contrib.internal.tornado import patch as tornado_patch  # noqa: E402

    # patch tornado if possible
    tornado_patch.patch()  # noqa: E402
except Exception:
    pass  # nosec

from pathlib import Path  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402

import pytest  # noqa: E402

# patch requests here: patch() is one-shot and installs the AppSec Session.request
# wrapper only if _load_modules is true, which a test may have turned off by then
import requests  # noqa: E402,F401

from ddtrace.internal.constants import FLASK_RESOURCE_FULL  # noqa: E402
from ddtrace.internal.settings.asm import config as asm_config  # noqa: E402
from tests.utils import TracerSpanContainer  # noqa: E402
from tests.utils import _build_tree  # noqa: E402


@pytest.fixture(scope="function", autouse=True)
def _dj_autoclear_mailbox() -> None:
    # Override the `_dj_autoclear_mailbox` test fixture in `pytest_django`.
    pass


@pytest.fixture
def test_spans(interface, check_waf_timeout):
    container = TracerSpanContainer(interface.tracer)
    assert check_waf_timeout is None
    yield container
    container.reset()


@pytest.fixture
def root_span(test_spans):
    # get the first root span
    def get_root_span():
        for span in test_spans.spans:
            if span.parent_id is None:
                return _build_tree(test_spans.spans, span)
        # In case root span is not found, try to find a span with a local root
        for span in test_spans.spans:
            if span._local_root is not None:
                return _build_tree(test_spans.spans, span._local_root)

    yield get_root_span


@pytest.fixture
def entry_span(test_spans):
    def get_entry_span():
        for span in test_spans.spans:
            if span._is_top_level and span.span_type == SpanTypes.WEB:
                return _build_tree(test_spans.spans, span)

        return None

    yield get_entry_span


@pytest.fixture
def check_waf_timeout(request):
    # change timeout to 50 seconds to avoid flaky timeouts
    previous_timeout = asm_config._waf_timeout
    asm_config._waf_timeout = 50_000.0
    yield
    asm_config._waf_timeout = previous_timeout


API10_SERVER_SCRIPT = str(Path(__file__).with_name("api10_server.py"))


class Api10Server:
    STARTUP_TIMEOUT = 30.0

    def __init__(self):
        self._process = None
        self._stderr = None
        self._port = 0

    def port(self) -> int:
        if self._process is None or self._process.poll() is not None:
            self.restart()
        return self._port

    def restart(self) -> None:
        self.stop()
        stderr = tempfile.TemporaryFile()
        with socket.create_server(("127.0.0.1", 0), backlog=128) as listener:
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", API10_SERVER_SCRIPT, str(listener.fileno())],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                pass_fds=(listener.fileno(),),
            )
            port = listener.getsockname()[1]
        self._process, self._stderr, self._port = process, stderr, port
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=self.STARTUP_TIMEOUT) as probe:
                probe.sendall(b"GET /request-headers HTTP/1.0\r\n\r\n")
                with probe.makefile("rb") as response:
                    status_line = response.readline()
        except OSError as e:
            status_line = repr(e).encode()
        if b" 200 " not in status_line:
            stderr.seek(0)
            message = f"api10 server not ready: {status_line!r}, exit code {process.poll()}, stderr: {stderr.read()!r}"
            self.stop()
            raise RuntimeError(message)

    def stop(self) -> None:
        if self._process is not None:
            with self._process:
                self._process.kill()
            self._process = None
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None


@pytest.fixture(scope="session")
def api10_server():
    server = Api10Server()
    yield server
    server.stop()


@pytest.fixture
def get_tag(test_spans, root_span):
    # checking both root spans and web spans for the tag
    def get(name):
        for span in test_spans.spans:
            if span.parent_id is None or span.span_type == "web":
                res = span.get_tag(name)
                if res is not None:
                    return res
        return root_span().get_tag(name)

    yield get


@pytest.fixture
def get_entry_span_tag(entry_span):
    def get(name):
        return entry_span().get_tag(name)

    yield get


@pytest.fixture
def get_metric(root_span):
    yield lambda name: root_span().get_metric(name)


@pytest.fixture
def get_entry_span_metric(entry_span):
    yield lambda name: entry_span().get_metric(name)


@pytest.fixture
def find_resource(test_spans, root_span):
    def find(resource_name):
        for span in test_spans.spans:
            if span.parent_id is None or span.span_type == "web":
                if span.resource == resource_name:
                    return True
                # Mounted Flask sub-apps keep ``span.resource`` app-local and expose the client-hit
                # resource on a side-channel tag for backend remapping.
                if span.get_tag(FLASK_RESOURCE_FULL) == resource_name:
                    return True
        return False

    yield find


def no_op(msg: str) -> None:  # noqa: ARG001
    """Do nothing."""


@pytest.fixture(name="printer")
def printer(request):
    terminal_reporter = request.config.pluginmanager.getplugin("terminalreporter")
    capture_manager = request.config.pluginmanager.get_plugin("capturemanager")

    def printer(*args, **kwargs):
        with capture_manager.global_and_fixture_disabled():
            if terminal_reporter is not None:  # pragma: no branch
                terminal_reporter.write_line(*args, **kwargs)

    return printer


def pytest_configure(config):
    config.addinivalue_line("markers", "xfail_interface: mark test to be xfailed for the given interface")
