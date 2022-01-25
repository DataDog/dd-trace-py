import contextlib
import os
import sys

import pytest

from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer
from tests.utils import call_program
from tests.utils import snapshot_context as _snapshot_context


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "snapshot(*args, **kwargs): mark test to run as a snapshot test which sends traces to the test agent"
    )


@pytest.fixture
def tracer():
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


def _request_token(request):
    token = ""
    token += request.module.__name__
    token += ".%s" % request.cls.__name__ if request.cls else ""
    token += ".%s" % request.node.name
    return token


@pytest.fixture(autouse=True)
def snapshot(request):
    marks = [m for m in request.node.iter_markers(name="snapshot")]
    assert len(marks) < 2, "Multiple snapshot marks detected"
    if marks:
        snap = marks[0]
        token = _request_token(request).replace(" ", "_").replace(os.path.sep, "_")
        with _snapshot_context(token, *snap.args, **snap.kwargs) as snapshot:
            yield snapshot
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
    token = _request_token(request)

    @contextlib.contextmanager
    def _snapshot(**kwargs):
        if "token" not in kwargs:
            kwargs["token"] = token
        with _snapshot_context(**kwargs) as snapshot:
            yield snapshot

    return _snapshot
