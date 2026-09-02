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

from http.server import BaseHTTPRequestHandler  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402
import os  # noqa: E402
import socket  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

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


@pytest.fixture
def api10_http_server_port(monkeypatch):
    class Api10Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._handle_request()

        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length:
                self.rfile.read(content_length)
            self._handle_request()

        def log_message(self, *args, **kwargs):
            pass  # silence test output

        def _handle_request(self):
            if self.path == "/request-headers":
                status = 200
                body = b"ok"
                headers = {"Content-Type": "text/plain"}
            elif self.path == "/response-headers":
                status = 200
                body = b"ok"
                headers = {"Content-Type": "text/plain", "x-api10-response": "api10-response-header"}
            elif self.path == "/response-body":
                status = 200
                body = b'{"payload": "api10-response-body"}'
                headers = {"Content-Type": "application/json"}
            elif self.path == "/response-status":
                status = 210
                body = b"ok"
                headers = {"Content-Type": "application/json"}
            elif self.path == "/redirect-source":
                status = 302
                body = b'{"payload": "api10-response-body"}'
                headers = {
                    "Content-Type": "application/json",
                    "Location": "/redirect-target",
                    "x-api10-redirect": "api10-redirect",
                }
            elif self.path == "/redirect-target":
                status = 200
                body = b'{"payload": "api10-response-body"}'
                headers = {"Content-Type": "application/json"}
            else:
                status = 404
                body = b"not found"
                headers = {"Content-Type": "text/plain"}

            self.send_response(status)
            for header, value in headers.items():
                self.send_header(header, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Api10Handler)
    _, port = server.server_address
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()

    deadline = time.monotonic() + 2.0
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            if time.monotonic() >= deadline:
                raise RuntimeError("api10 http server failed to start")
            time.sleep(0.01)

    yield port
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


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


def _cgroup(path):
    """Read a cgroup file, or a marker naming the failure.

    These paths are cgroup v2. On a cgroup v1 host the same values live under per-controller
    directories with different names, so they come back as <FileNotFoundError> and only the
    benchmarks stay meaningful. Non-Linux never reaches here, see _env_probe.
    """
    try:
        with open(path) as f:
            return f.read().strip().replace("\n", " | ")
    except Exception as exc:
        return f"<{exc.__class__.__name__}>"


def _env_probe(label):
    """Report what the machine gave this job, so a slow run can be attributed rather than guessed at.

    Called at session start and end; most of the value is in diffing the two. Skipped entirely off
    Linux, where neither the cgroup files nor sched_getaffinity exist; these suites always run in
    the Linux testrunner container, locally and in CI alike.

    affinity / cpu_count
        How many CPUs the process may actually use. Separates "few cores" from "throttled": a job
        pinned to one core and a job throttled to a fraction of 96 both look slow but need
        different fixes. affinity needs sched_getaffinity, which is Linux only.

    cpu.max
        The CFS quota and period, e.g. "25000 100000" for a quarter core. A literal "max" means no
        quota at all, which also means quota throttling cannot be happening, so the counters below
        will stay at zero however contended the node is.

    cpu.stat
        usage_usec is the CPU time the container has consumed. Diffing it between the two probes and
        comparing against wall clock gives utilisation, which is the one number that separates
        "computing hard" from "sitting in a blocking call". A job at 24% is waiting on something;
        a job near 100% is genuinely compute bound. nr_throttled and throttled_usec attribute any
        stalling to quota enforcement, and are only ever non-zero when cpu.max sets a quota.

    memory.max / memory.current
        The limit this pod was given, against what it actually used. Worth watching where limits are
        autoscaled rather than declared: a peak sitting close to the limit predicts intermittent
        OOM kills, which surface as flaky infrastructure failures rather than test failures.

    BENCH cpu_loop_5M
        Pure-Python arithmetic throughput, no syscalls or allocation. The baseline for comparing one
        runner against another, or against a laptop. Expect it to vary by more than 2x between
        runners in the same pipeline, so read single-shard timings with that in mind.

    BENCH syscall_getpid_200k
        The cost of entering the kernel, using about the cheapest syscall there is. Isolates syscall
        overhead from the work done in a syscall, which matters under seccomp or gVisor style
        sandboxing.

    BENCH stat_20k
        Filesystem metadata cost. Sensitive to how many overlay and bind mount layers the checkout
        sits behind, which is usually the difference between a container and a host filesystem.
    """
    if not sys.platform.startswith("linux"):
        return

    import time as _time

    lines = [f"===== RUNTIME PROBE [{label}] ====="]
    lines.append(f"affinity={len(os.sched_getaffinity(0))} cpu_count={os.cpu_count()}")
    for path in (
        "/sys/fs/cgroup/cpu.max",
        "/sys/fs/cgroup/cpu.stat",
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory.current",
    ):
        lines.append(f"{path} = {_cgroup(path)}")

    t0 = _time.perf_counter()
    acc = 0
    for i in range(5_000_000):
        acc += i * i
    lines.append(f"BENCH cpu_loop_5M = {_time.perf_counter() - t0:.3f}s")

    t0 = _time.perf_counter()
    for _ in range(200_000):
        os.getpid()
    lines.append(f"BENCH syscall_getpid_200k = {_time.perf_counter() - t0:.3f}s")

    t0 = _time.perf_counter()
    for _ in range(20_000):
        os.stat(__file__)
    lines.append(f"BENCH stat_20k = {_time.perf_counter() - t0:.3f}s")

    lines.append("===== END RUNTIME PROBE =====")
    print("\n" + "\n".join(lines), flush=True)


def pytest_configure(config):
    config.addinivalue_line("markers", "xfail_interface: mark test to be xfailed for the given interface")
    _env_probe("session start")


def pytest_sessionfinish(session, exitstatus):
    _env_probe("session end")
