import linecache
from itertools import count
from types import FunctionType

from tests.utils import PY, DataSummary, run_target


def get_line_number(function: FunctionType, content: str) -> int:
    code = function.__code__
    start = code.co_firstlineno
    filename = code.co_filename

    for i in count(start):
        line = linecache.getline(filename, i)

        if line == "":
            raise ValueError("Line not found")

        if content in line:
            return i

    raise ValueError("Line not found")


def test_gevent():
    import echion.monkey.gevent as _gevent

    result, data = run_target("target_gevent")
    assert result.returncode == 0, result.stderr.decode()

    md = data.metadata
    assert md["mode"] == "wall"
    assert md["interval"] == "1000"

    summary = DataSummary(data)

    expected_nthreads = 2
    assert summary.nthreads == expected_nthreads, summary.threads
    assert summary.total_metric >= 1.4 * 1e6

    greenlet_join = "Greenlet.join" if PY >= (3, 11) else "join"
    greenlet_join_line_number = get_line_number(
        _gevent.Greenlet.join, "super().join(*args, **kwargs)"
    )

    # Test line numbers
    assert summary.query("0:MainThread", (("Greenlet-1", 0), ("f2", 16))) is not None, (
        summary.threads
    )
    assert summary.query("0:MainThread", (("Greenlet-0", 0), ("f3", 22))) is not None
    assert (
        summary.query(
            "0:MainThread",
            (
                ("Greenlet-0", 0),
                ("f3", 23),
                (greenlet_join, greenlet_join_line_number),
                ("Greenlet-2", 0),
                ("f1", 10),
            ),
        )
        is not None
    )
