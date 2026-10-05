def assert_stats(rec, **outcomes):
    """
    Assert that the correct number of test results of each type is present in a test run.

    This is similar to `rec.assertoutcome()`, but works with test statuses other than 'passed', 'failed' and 'skipped'.
    """
    stats = {**rec.getcall("pytest_terminal_summary").terminalreporter.stats}
    stats.pop("", None)

    for outcome, expected_count in outcomes.items():
        actual_count = len(stats.pop(outcome, []))
        assert actual_count == expected_count, f"Expected {expected_count} {outcome} tests, got {actual_count}"

    # NOTE: warnings are terminal-report metadata, not test outcomes. Nested
    # pytest runs can legitimately warn when an outer retry reuses the same Pytester
    # fixture, so outcome assertions must not depend on the ambient warning set.
    stats.pop("warnings", None)

    assert not stats, f"Found unexpected stats in test results: {', '.join(stats.keys())}"
