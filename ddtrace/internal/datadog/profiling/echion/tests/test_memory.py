from tests.utils import DataSummary, run_target


def test_memory():
    result, data = run_target("target_mem", "-m")
    assert result.returncode == 0, result.stderr.decode()

    md = data.metadata
    assert md["mode"] == "memory"
    assert md["interval"] == "1000"

    summary = DataSummary(data)

    expected_nthreads = 1
    assert summary.nthreads == expected_nthreads

    assert summary.query("0:MainThread", (("<module>", 25), ("leak", 21))) is not None, summary.threads["0:MainThread"]
