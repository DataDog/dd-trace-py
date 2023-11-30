import ast
import base64
import contextlib
import importlib
from itertools import product
import json
import os
from os.path import split
from os.path import splitext
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time
from typing import Any  # noqa:F401
from typing import Generator  # noqa:F401
from typing import Tuple  # noqa:F401
from unittest import mock

from _pytest.runner import call_and_report
from _pytest.runner import pytest_runtest_protocol as default_pytest_runtest_protocol
import attr
import pytest

import ddtrace
from ddtrace.internal.compat import httplib
from ddtrace.internal.compat import parse
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.service import ServiceStatusError
from ddtrace.internal.telemetry import TelemetryWriter
from ddtrace.internal.utils.formats import parse_tags_str  # noqa:F401
from tests import utils
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer
from tests.utils import call_program
from tests.utils import request_token
from tests.utils import snapshot_context as _snapshot_context


code_to_pyc = getattr(importlib._bootstrap_external, "_code_to_timestamp_pyc")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "snapshot(*args, **kwargs): mark test to run as a snapshot test which sends traces to the test agent"
    )


@pytest.fixture
def use_global_tracer():
    yield False


@pytest.fixture
def tracer(use_global_tracer):
    if use_global_tracer:
        return ddtrace.tracer
    else:
        return DummyTracer()


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()


@pytest.fixture
def run_python_code_in_subprocess(tmpdir):
    def _run(code, **kwargs):
        pyfile = tmpdir.join("test.py")
        pyfile.write(code)
        return call_program(sys.executable, str(pyfile), **kwargs)

    yield _run


@pytest.fixture
def ddtrace_run_python_code_in_subprocess(tmpdir):
    def _run(code, **kwargs):
        pyfile = tmpdir.join("test.py")
        pyfile.write(code)
        return call_program("ddtrace-run", sys.executable, str(pyfile), **kwargs)

    yield _run


@pytest.fixture(autouse=True)
def snapshot(request):
    marks = [m for m in request.node.iter_markers(name="snapshot")]
    assert len(marks) < 2, "Multiple snapshot marks detected"
    if marks and os.getenv("DD_SNAPSHOT_ENABLED", "1") == "1":
        snap = marks[0]
        token = snap.kwargs.get("token")
        if token:
            del snap.kwargs["token"]
        else:
            token = request_token(request).replace(" ", "_").replace(os.path.sep, "_")

        mgr = _snapshot_context(token, *snap.args, **snap.kwargs)
        snapshot = mgr.__enter__()
        yield snapshot
        # Skip doing any checks if the test was skipped
        if hasattr(request.node, "rep_call") and not request.node.rep_call.skipped:
            mgr.__exit__(None, None, None)
    else:
        yield


@pytest.fixture
def snapshot_context(request):
    """
    Fixture to provide a context manager for executing code within a ``tests.utils.snapshot_context``
    with a default ``token`` based on the test function/``pytest`` request.

    def test_case(snapshot_context):
        with snapshot_context():
            # my code
    """
    token = request_token(request)

    @contextlib.contextmanager
    def _snapshot(**kwargs):
        if "token" not in kwargs:
            kwargs["token"] = token
        with _snapshot_context(**kwargs) as snapshot:
            yield snapshot

    return _snapshot


def dump_code_to_file(code, file):
    file.write(code_to_pyc(code, time.time(), len(code.co_code)))
    file.flush()


def unwind_params(params):
    if params is None:
        yield None
        return

    for _ in product(*([(k, v) for v in vs] for k, vs in params.items())):
        yield dict(_)


class FunctionDefFinder(ast.NodeVisitor):
    def __init__(self, func_name):
        super(FunctionDefFinder, self).__init__()
        self.func_name = func_name
        self._body = None

    def generic_visit(self, node):
        return self._body or super(FunctionDefFinder, self).generic_visit(node)

    def visit_FunctionDef(self, node):
        if node.name == self.func_name:
            self._body = node.body

    def find(self, file):
        with open(file) as f:
            t = ast.parse(f.read())
            self.visit(t)
            t.body = self._body
            return t


def is_stream_ok(stream, expected):
    if expected is None:
        return True

    if isinstance(expected, str):
        ex = expected.encode("utf-8")
    elif isinstance(expected, bytes):
        ex = expected
    else:
        # Assume it's a callable condition
        return expected(stream.decode("utf-8"))

    return stream == ex


def run_function_from_file(item, params=None):
    file, _, func = item.location
    marker = item.get_closest_marker("subprocess")
    run_module = marker.kwargs.get("run_module", False)

    args = [sys.executable]

    timeout = marker.kwargs.get("timeout", None)

    # Add ddtrace-run prefix in ddtrace-run mode
    if marker.kwargs.get("ddtrace_run", False):
        args.insert(0, "ddtrace-run")

    # Add -m if running script as a module
    if run_module:
        args.append("-m")

    # Override environment variables for the subprocess
    env = os.environ.copy()
    pythonpath = os.getenv("PYTHONPATH", None)
    base_path = os.path.dirname(os.path.dirname(__file__))
    env["PYTHONPATH"] = os.pathsep.join((base_path, pythonpath)) if pythonpath is not None else base_path

    for key, value in marker.kwargs.get("env", {}).items():
        if value is None:  # None means remove the variable
            env.pop(key, None)
        else:
            env[key] = value

    if params is not None:
        env.update(params)

    expected_status = marker.kwargs.get("status", 0)
    expected_out = marker.kwargs.get("out", "")
    expected_err = marker.kwargs.get("err", "")

    with NamedTemporaryFile(mode="wb", suffix=".pyc") as fp:
        dump_code_to_file(compile(FunctionDefFinder(func).find(file), file, "exec"), fp.file)

        # If running a module with -m, we change directory to the module's
        # folder and run the module directly.
        if run_module:
            cwd, module = split(splitext(fp.name)[0])
            args.append(module)
        else:
            cwd = None
            args.append(fp.name)

        # Add any extra requested args
        args.extend(marker.kwargs.get("args", []))

        def _subprocess_wrapper():
            out, err, status, _ = call_program(*args, env=env, cwd=cwd, timeout=timeout)

            if status != expected_status:
                raise AssertionError(
                    "Expected status %s, got %s."
                    "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
                    "\n=== Captured STDERR ===\n%s=== End of captured STDERR ==="
                    % (expected_status, status, out.decode("utf-8"), err.decode("utf-8"))
                )

            if not is_stream_ok(out, expected_out):
                raise AssertionError("STDOUT: Expected [%s] got [%s]" % (expected_out, out))

            if not is_stream_ok(err, expected_err):
                raise AssertionError("STDERR: Expected [%s] got [%s]" % (expected_err, err))

        return _subprocess_wrapper()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item):
    if item.get_closest_marker("skip"):
        return default_pytest_runtest_protocol(item, None)

    skipif = item.get_closest_marker("skipif")
    if skipif and skipif.args[0]:
        return default_pytest_runtest_protocol(item, None)

    marker = item.get_closest_marker("subprocess")
    if marker:
        params = marker.kwargs.get("parametrize", None)
        ihook = item.ihook
        base_name = item.nodeid

        for ps in unwind_params(params):
            nodeid = (base_name + str(ps)) if ps is not None else base_name

            # Start
            ihook.pytest_runtest_logstart(nodeid=nodeid, location=item.location)

            # Setup
            report = call_and_report(item, "setup", log=False)
            report.nodeid = nodeid
            ihook.pytest_runtest_logreport(report=report)

            # Call
            item.runtest = lambda: run_function_from_file(item, ps)  # noqa: B023
            report = call_and_report(item, "call", log=False)
            report.nodeid = nodeid
            ihook.pytest_runtest_logreport(report=report)

            # Teardown
            report = call_and_report(item, "teardown", log=False, nextitem=None)
            report.nodeid = nodeid
            ihook.pytest_runtest_logreport(report=report)

            # Finish
            ihook.pytest_runtest_logfinish(nodeid=nodeid, location=item.location)

        return True


def _run(cmd):
    return subprocess.check_output(cmd, shell=True)


@contextlib.contextmanager
def create_package(directory, pyproject, setup):
    package_dir = os.path.join(directory, "mypackage")
    os.mkdir(package_dir)

    pyproject_file = os.path.join(package_dir, "pyproject.toml")
    with open(pyproject_file, "wb") as f:
        f.write(pyproject.encode("utf-8"))

    setup_file = os.path.join(package_dir, "setup.py")
    with open(setup_file, "wb") as f:
        f.write(setup.encode("utf-8"))

    _ = os.path.join(package_dir, "mypackage")
    os.mkdir(_)
    with open(os.path.join(_, "__init__.py"), "wb") as f:
        f.write('"0.0.1"'.encode("utf-8"))

    cwd = os.getcwd()
    os.chdir(package_dir)

    try:
        _run("git init")
        _run("git config --local user.name user")
        _run("git config --local user.email user@company.com")
        _run("git add .")
        _run("git commit --no-gpg-sign -m init")
        _run("git remote add origin https://username:password@github.com/companydotcom/repo.git")

        yield package_dir
    finally:
        os.chdir(cwd)


@pytest.fixture
def mypackage_example(tmpdir):
    with create_package(
        str(tmpdir),
        """\
[build-system]
requires = ["setuptools", "ddtrace"]
build-backend = "setuptools.build_meta"
""",
        """\
import ddtrace.sourcecode.setuptools_auto
from setuptools import setup

setup(
    name="mypackage",
    version="0.0.1",
)
""",
    ) as package:
        yield package


@pytest.fixture
def git_repo_empty(tmpdir):
    yield utils.git_repo_empty(tmpdir)


@pytest.fixture
def git_repo(git_repo_empty):
    yield utils.git_repo(git_repo_empty)


def _stop_remote_config_worker():
    if remoteconfig_poller._worker:
        remoteconfig_poller._stop_service(True)
        remoteconfig_poller._worker = None


@pytest.fixture
def remote_config_worker():
    try:
        remoteconfig_poller.disable(join=True)
    except ServiceStatusError:
        pass
    remoteconfig_poller._client = RemoteConfigClient()
    try:
        yield
    finally:
        _stop_remote_config_worker()

    # Check remote config poller and Subscriber threads stop correctly
    # we have 2 threads: main thread and telemetry thread. TODO: verify if that alive thread is a bug
    # TODO: this assert doesn't work in CI, threading.active_count() > 50
    # assert threading.active_count() == 2


@pytest.fixture
def telemetry_writer():
    telemetry_writer = TelemetryWriter(is_periodic=False)
    telemetry_writer.enable()

    with mock.patch("ddtrace.internal.telemetry.telemetry_writer", telemetry_writer):
        yield telemetry_writer


@attr.s
class TelemetryTestSession(object):
    token = attr.ib(type=str)
    telemetry_writer = attr.ib(type=TelemetryWriter)

    def create_connection(self):
        parsed = parse.urlparse(self.telemetry_writer._client._agent_url)
        return httplib.HTTPConnection(parsed.hostname, parsed.port)

    def _request(self, method, url):
        # type: (str, str) -> Tuple[int, bytes]
        conn = self.create_connection()
        try:
            conn.request(method, url)
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def clear(self):
        status, _ = self._request("GET", "/test/session/clear?test_session_token=%s" % self.token)
        if status != 200:
            pytest.fail("Failed to clear session: %s" % self.token)
        return True

    def get_requests(self):
        """Get a list of the requests sent to the test agent

        Results are in reverse order by ``seq_id``
        """
        status, body = self._request("GET", "/test/session/requests?test_session_token=%s" % self.token)

        if status != 200:
            pytest.fail("Failed to fetch session requests: %s %s %s" % (self.create_connection(), status, self.token))
        requests = json.loads(body.decode("utf-8"))
        for req in requests:
            body_str = base64.b64decode(req["body"]).decode("utf-8")
            req["body"] = json.loads(body_str)

        return sorted(requests, key=lambda r: r["body"]["seq_id"], reverse=True)

    def get_events(self):
        """Get a list of the event payloads sent to the test agent

        Results are in reverse order by ``seq_id``
        """
        status, body = self._request("GET", "/test/session/apmtelemetry?test_session_token=%s" % self.token)
        if status != 200:
            pytest.fail("Failed to fetch session events: %s" % self.token)
        return sorted(json.loads(body.decode("utf-8")), key=lambda e: e["seq_id"], reverse=True)


@pytest.fixture
def test_agent_session(telemetry_writer, request):
    # type: (TelemetryWriter, Any) -> Generator[TelemetryTestSession, None, None]
    token = request_token(request)
    telemetry_writer._restart_sequence()
    telemetry_writer._client._headers["X-Datadog-Test-Session-Token"] = token

    requests = TelemetryTestSession(token=token, telemetry_writer=telemetry_writer)

    conn = requests.create_connection()
    try:
        conn.request("GET", "/test/session/start?test_session_token=%s" % token)
        conn.getresponse()
    finally:
        conn.close()

    try:
        yield requests
    finally:
        telemetry_writer.reset_queues()
