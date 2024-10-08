# mypy: ignore-errors
from itertools import product
import os
from pathlib import Path
import sys
from unittest.mock import MagicMock


sys.modules["ddtrace"] = MagicMock()
sys.modules["ddtrace"].__version__ = "0.0.0"
sys.modules["ddtrace.internal"] = MagicMock()
sys.modules["ddtrace.internal.compat"] = MagicMock()
sys.modules["ddtrace.internal.constants"] = MagicMock()
sys.modules["ddtrace.internal.datadog"] = MagicMock()
sys.modules["ddtrace.internal.datadog.profiling"] = MagicMock()
sys.modules["ddtrace.internal.datadog.profiling.ddup"] = MagicMock()
sys.modules["ddtrace.internal.datadog.profiling.ddup.utils"] = MagicMock()
sys.modules["ddtrace.internal.runtime"] = MagicMock()
sys.modules["ddtrace.internal.runtime.compat"] = MagicMock()
sys.modules["ddtrace.internal.runtime.constants"] = MagicMock()
sys.modules["ddtrace._trace"] = MagicMock()
sys.modules["ddtrace._trace.span"] = MagicMock()


# Setup the Span object
# This is terrible not-quite-copypasta, but this is just a quick-and-dirty harness.
# This will get replaced in the next iteration
class Span(object):
    def __init__(
        self,
        span_id=None,  # type: Optional[int]
        service=None,  # type: Union[None, str, bytes]
        span_type=None,  # type: Union[None, str, bytes]
        _local_root=None,  # type: Optional[Span]
    ):
        self.span_id = span_id
        self.service = service
        self.span_type = span_type
        self._local_root = _local_root


sys.modules["ddtrace._trace.span"].Span = Span


# The location of the ddup folder as it is in the dd-trace-py library
ddup_path = Path(__file__).parent.resolve() / ".." / ".." / "build" / "ddup"
sys.path.insert(0, str(ddup_path))
import _ddup  # noqa


# Function for running a test in a fork
# Returns true if there were no exceptions, else false
def run_test(test: callable) -> bool:
    pid = os.fork()
    if pid == 0:
        try:
            test()
            os._exit(0)
        except Exception as e:
            print(e)
            raise e
            os._exit(1)
    else:
        _, status = os.waitpid(pid, 0)
        return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


# Initialization tests; we run every single type combination
def InitTest(name, tags, value):
    def test():
        _ddup.init(
            service=name,
            env=name,
            version=name,
            tags=tags,
            max_nframes=value,
            url=name,
        )

    return test


# 3 * 5 * 13 = 195 tests
InitTypeTests = [
    InitTest(name, tags, value)
    for name, tags, value in product(
        [None, b"name", "name"],
        [None, {"tag": "value"}, {"tag": b"value"}, {b"tag": "value"}, {b"tag": b"value"}],
        [
            0,
            -1,
            1,
            256,
            2**30 + 1,
            2**48 - 1,
            2**48,
            2**48 + 1,
            2**62 - 1,
            2**62,
            2**62 + 1,
            2**64 - 1,
            2**64,
            2**64 + 1,
        ],  # values
    )
]

InitNormal = InitTest("name", {"tag": "value"}, 10)


# Test all sample interfaces
def SampleTestSimple():
    InitNormal()
    h = _ddup.SampleHandle()
    h.push_walltime(1, 1)
    h.push_cputime(1, 1)
    h.push_acquire(1, 1)
    h.push_release(1, 1)
    h.push_alloc(1, 1)
    h.push_heap(1)
    h.flush_sample()


def SampleTest(value, name, exc_type, lineno, span, endpoint):
    def test():
        try:
            InitNormal()
            h = _ddup.SampleHandle()
            h.push_walltime(value, 1)
            h.push_cputime(value, 1)
            h.push_acquire(value, 1)
            h.push_release(value, 1)
            h.push_alloc(value, 1)
            h.push_heap(value)
            h.push_threadinfo(value, value, name)
            h.push_task_id(value)
            h.push_task_name(name)
            h.push_exceptioninfo(exc_type, value)
            h.push_span(span)
            h.push_frame(name, name, value, lineno)
            h.flush_sample()
        except Exception as e:
            # Just print the exception, including the line and stuff
            print("Exception in test:")
            print(e)
            raise e

    return test


def MakeSpan(span_id, service, span_type, add_local_root):
    return Span(span_id, service, span_type, Span(span_id, service, span_type) if add_local_root else None)


# 13 * 3 * 3 * 5 * 2 * 3 = 7020 tests
SampleTypeTests = [
    SampleTest(value, exc_type, name, lineno, MakeSpan(value, name, name, add_local_root), endpoint)
    for value, exc_type, name, lineno, add_local_root, endpoint in product(
        [
            0,
            -1,
            1,
            256,
            2**30 + 1,
            2**48 - 1,
            2**48,
            2**48 + 1,
            2**62 - 1,
            2**62,
            2**62 + 1,
            2**64 - 1,
            2**64,
            2**64 + 1,
        ],  # values
        [None, Exception, ValueError],  # exc_type
        [None, b"name", "name"],  # names
        [-1, 0, 1, 256, 1000],  # lineno
        [False, True],  # add_local_root
        [None, False, True],  # endpoint
    )
]


# Run the init tests
for test in InitTypeTests:
    assert run_test(test)
assert run_test(InitNormal)
assert run_test(SampleTestSimple)
for test in SampleTypeTests:
    assert run_test(test)
