import ast
import contextlib
from itertools import product
import os
from os.path import split
from os.path import splitext
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time

from _pytest.runner import CallInfo
from _pytest.runner import TestReport
from _pytest.runner import call_and_report
from _pytest.runner import pytest_runtest_protocol as default_pytest_runtest_protocol
import pytest
from six import PY2

import ddtrace
from ddtrace.internal.remoteconfig.client import RemoteConfigClient
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from tests import utils
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer
from tests.utils import call_program
from tests.utils import request_token
from tests.utils import snapshot_context as _snapshot_context


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


# DEV: The dump_code_to_file function is adapted from the compile function in
# the py_compile module of the Python standard library. It generates .pyc files
# with the right format.
if PY2:
    import marshal
    from py_compile import MAGIC
    from py_compile import wr_long

    def dump_code_to_file(code, file):
        file.write(MAGIC)
        wr_long(file, long(time.time()))  # noqa
        marshal.dump(code, file)
        file.flush()


else:
    import importlib

    code_to_pyc = getattr(
        importlib._bootstrap_external, "_code_to_bytecode" if sys.version_info < (3, 7) else "_code_to_timestamp_pyc"
    )

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

        return TestReport.from_item_and_call(item, CallInfo.from_call(_subprocess_wrapper, "call"))


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
            report = run_function_from_file(item, ps)
            report.nodeid = nodeid
            ihook.pytest_runtest_logreport(report=report)

            # Teardown
            report = call_and_report(item, "teardown", log=False, nextitem=None)
            report.nodeid = nodeid
            ihook.pytest_runtest_logreport(report=report)

            # Finish
            ihook.pytest_runtest_logfinish(nodeid=nodeid, location=item.location)

        return True


# source code fixtures


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
        _run("git remote add origin https://github.com/companydotcom/repo.git")

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
        remoteconfig_poller._stop_service()
        remoteconfig_poller._worker = None


@pytest.fixture
def remote_config_worker():
    remoteconfig_poller.disable()
    remoteconfig_poller._client = RemoteConfigClient()
    try:
        yield
    finally:
        _stop_remote_config_worker()
