import sys

from tests.utils import PY
from tests.utils import DataSummary
from tests.utils import run_target


def test_asyncio_gather_wall_time():
    result, data = run_target("target_gather")
    assert result.returncode == 0, result.stderr.decode()

    md = data.metadata
    assert md["mode"] == "wall"
    assert md["interval"] == "1000"

    summary = DataSummary(data)

    expected_nthreads = 2
    assert summary.nthreads == expected_nthreads, summary.threads
    assert summary.total_metric >= 1.4 * 1e6

    # Test line numbers
    assert (
        summary.query("0:MainThread", (("F4_0", 0), ("f4", 22), ("f5", 26))) is not None
    )
    assert (
        summary.query("0:MainThread", (("F4_1", 0), ("f4", 22), ("f5", 26))) is not None
    )

    # Test stacks and expected values
    if PY >= (3, 11):
        for t in ("F4_0", "F4_1"):
            summary.assert_substack(
                "0:MainThread",
                (
                    "_run_module_as_main",
                    "_run_code",
                    "<module>",
                    "run",
                    "Runner.run",
                    "BaseEventLoop.run_until_complete",
                    "BaseEventLoop.run_forever",
                    "BaseEventLoop._run_once",
                    "KqueueSelector.select"
                    if sys.platform == "darwin"
                    else "EpollSelector.select",
                    "Task-1",
                    "main",
                    "F1",
                    "f1",
                    "f2",
                    "F3",
                    "f3",
                    t,
                    "f4",
                    "f5",
                    "sleep",
                ),
                lambda v: v >= 0.45e6,
            )
    else:
        for t in ("F4_0", "F4_1"):
            summary.assert_substack(
                "0:MainThread",
                (
                    "_run_module_as_main",
                    "_run_code",
                    "<module>",
                    "run",
                    "run_until_complete",
                    "run_forever",
                    "_run_once",
                    "select",
                    "Task-1",
                    "main",
                    "F1",
                    "f1",
                    "f2",
                    "F3",
                    "f3",
                    t,
                    "f4",
                    "f5",
                    "sleep",
                ),
                lambda v: v >= 0.45e6,
            )
