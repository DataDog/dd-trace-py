import pytest

from tests.utils import PY
from tests.utils import DataSummary
from tests.utils import run_target
from tests.utils import stealth


@stealth
def test_cpu_time(stealth):
    result, data = run_target("target_cpu", *stealth, "-c")
    assert result.returncode == 0 and data, result.stderr.decode()

    md = data.metadata
    assert md["mode"] == "cpu"
    assert md["interval"] == "1000"

    summary = DataSummary(data)

    expected_nthreads = 3 - bool(stealth)
    assert summary.nthreads == expected_nthreads
    assert summary.total_metric >= 0.5 * 1e6
    assert summary.nsamples

    # Test line numbers
    assert summary.query("0:MainThread", (("main", 22), ("bar", 17))) is None
    assert (
        summary.query(
            "0:SecondaryThread",
            ("Thread.run" if PY >= (3, 11) else "run", "keep_cpu_busy"),
        )
        is not None
    )

    # Test stacks and expected values
    summary.assert_stack(
        "0:MainThread",
        (
            "_run_module_as_main",
            "_run_code",
            "<module>",
            "keep_cpu_busy",
        ),
        lambda v: v >= 3e5,
    )

    if PY >= (3, 11):
        summary.assert_stack(
            "0:SecondaryThread",
            (
                "Thread._bootstrap",
                "thread_bootstrap_inner",
                "Thread._bootstrap_inner",
                "Thread.run",
                "keep_cpu_busy",
            ),
            lambda v: v >= 3e5,
        )
    else:
        summary.assert_stack(
            "0:SecondaryThread",
            (
                "_bootstrap",
                "thread_bootstrap_inner",
                "_bootstrap_inner",
                "run",
                "keep_cpu_busy",
            ),
            lambda v: v >= 3e5,
        )


@stealth
@pytest.mark.xfail
def test_cpu_time_native(stealth):
    result, data = run_target("target_cpu", *stealth, "-cn")
    assert result.returncode == 0, result.stderr.decode()

    md = data.metadata
    assert md["mode"] == "cpu"
    assert md["interval"] == "1000"

    summary = DataSummary(data)

    expected_nthreads = 3 - bool(stealth)
    assert summary.nthreads == expected_nthreads
    assert summary.total_metric

    # Test line numbers. This only works with CFrame
    if PY >= (3, 11):
        assert (
            summary.query(
                "0:MainThread",
                (
                    ("<module>", 48),
                    ("keep_cpu_busy", 39),
                ),
            )
            is not None
        ), summary.threads["0:MainThread"]
        assert summary.query("0:SecondaryThread", (("keep_cpu_busy", 39),)) is not None
    else:
        assert summary.query("0:MainThread", (("keep_cpu_busy", 39),)) is not None
        assert summary.query("0:SecondaryThread", (("keep_cpu_busy", 39),)) is not None
