import pytest

from tests.utils import PY
from tests.utils import DataSummary
from tests.utils import run_target
from tests.utils import stealth


@stealth
def test_wall_time(stealth):
    result, data = run_target("target", *stealth)
    assert result.returncode == 0, result.stderr.decode()

    md = data.metadata
    assert md["mode"] == "wall"
    assert md["interval"] == "1000"

    summary = DataSummary(data)

    expected_nthreads = 3 - bool(stealth)
    assert summary.nthreads == expected_nthreads
    assert summary.total_metric >= 1e6 * expected_nthreads

    # Test line numbers
    assert summary.query("0:MainThread", (("main", 22), ("bar", 17))) is not None
    assert summary.query("0:SecondaryThread", (("bar", 18), ("foo", 13))) is not None

    # Test stacks and expected values
    summary.assert_stack(
        "0:MainThread",
        (
            "_run_module_as_main",
            "_run_code",
            "<module>",
            "main",
            "bar",
        ),
        lambda v: v >= 0.95e6,
    )
    summary.assert_stack(
        "0:MainThread",
        (
            "_run_module_as_main",
            "_run_code",
            "<module>",
            "main",
            "bar",
            "foo",
            "cpu_sleep",
        ),
        lambda v: v >= 4.5e5,
    )

    if PY >= (3, 11):
        summary.assert_stack(
            "0:SecondaryThread",
            (
                "Thread._bootstrap",
                "thread_bootstrap_inner",
                "Thread._bootstrap_inner",
                "Thread.run",
                "main",
                "bar",
            ),
            lambda v: v >= 0.95e6,
        )
        summary.assert_stack(
            "0:SecondaryThread",
            (
                "Thread._bootstrap",
                "thread_bootstrap_inner",
                "Thread._bootstrap_inner",
                "Thread.run",
                "main",
                "bar",
                "foo",
                "cpu_sleep",
            ),
            lambda v: v >= 4.5e5,
        )

        if not bool(stealth):
            summary.assert_stack(
                "0:echion.core.sampler",
                (
                    "Thread._bootstrap",
                    "thread_bootstrap_inner",
                    "Thread._bootstrap_inner",
                    "Thread.run",
                ),
                lambda v: v >= 1.45e6,
            )
    else:
        summary.assert_stack(
            "0:SecondaryThread",
            (
                "_bootstrap",
                "thread_bootstrap_inner",
                "_bootstrap_inner",
                "run",
                "main",
                "bar",
            ),
            lambda v: v >= 0.95e6,
        )
        summary.assert_stack(
            "0:SecondaryThread",
            (
                "_bootstrap",
                "thread_bootstrap_inner",
                "_bootstrap_inner",
                "run",
                "main",
                "bar",
                "foo",
                "cpu_sleep",
            ),
            lambda v: v >= 4.5e5,
        )

        if not bool(stealth):
            summary.assert_stack(
                "0:echion.core.sampler",
                (
                    "_bootstrap",
                    "thread_bootstrap_inner",
                    "_bootstrap_inner",
                    "run",
                ),
                lambda v: v >= 1.45e6,
            )


@stealth
@pytest.mark.xfail
def test_wall_time_native(stealth):
    result, data = run_target("target", *stealth, "-n")
    assert result.returncode == 0, result.stderr.decode()

    md = data.metadata
    assert md["mode"] == "wall"
    assert md["interval"] == "1000"

    summary = DataSummary(data)

    expected_nthreads = 3 - bool(stealth)
    assert summary.nthreads == expected_nthreads
    assert summary.total_metric

    # Test line numbers. This only works with CFrames
    if PY >= (3, 11):
        assert summary.query("0:MainThread", (("main", 22), ("bar", 17))) is not None
        assert (
            summary.query("0:SecondaryThread", (("bar", 18), ("foo", 13))) is not None
        )
    else:
        assert summary.query("0:MainThread", (("bar", 17),)) is not None
        assert summary.query("0:SecondaryThread", (("foo", 13),)) is not None
