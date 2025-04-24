def assert_stats(rec, **outcomes):
    """
    Assert that the correct number of test results of each type is present in a test run.

    This is similar to `rec.assertoutcome()`, but works with test statuses other than 'passed', 'failed' and 'skipped'.
    """
    stats = {**rec.getcall("pytest_terminal_summary").terminalreporter.stats}
    stats.pop("", None)
    stats.pop("warnings", None)

    breakpoint()
    for outcome, expected_count in outcomes.items():
        actual_count = len(stats.pop(outcome, []))
        assert actual_count == expected_count, f"Expected {expected_count} {outcome} tests, got {actual_count}"

    assert not stats, f"Found unexpected stats in test results: {', '.join(stats.keys())}"
