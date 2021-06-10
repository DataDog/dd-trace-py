import sys

import pytest

from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer
from tests.utils import call_program


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
