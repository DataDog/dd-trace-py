"""Minimal proof that RetryReports loses information about teardown failures.

Mirrors the relevant logic from `ddtrace/testing/internal/pytest/plugin.py`
verbatim (RetryReports.log_test_report, the call/setup mark-as-retry rule,
and _get_test_outcome's setup/call/teardown walk). Then constructs a single
test attempt with the shape "setup pass, call pass, teardown fail" and shows
the two systems disagree:

  - test_run.status reads all three phases and concludes FAIL.
  - reports_by_outcome only ever sees the call (or setup) report and ends up
    with the entry under "passed".

If a retry handler then says final_status=FAIL, make_final_report looks up
reports_by_outcome["failed"][0] and gets IndexError. That's the chain we see
in CI: IndexError fallback returns longrepr=None, junitxml then crashes with
`assert report.longrepr is not None`, and pytest_runtest_protocol aborts.

Run: python proof_teardown_bookkeeping_bug.py
"""
from collections import defaultdict


# === inlined from ddtrace.testing.internal.test_data ===
class TestStatus:
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


# === inlined from ddtrace.testing.internal.pytest.plugin ===
class TestPhase:
    SETUP = "setup"
    CALL = "call"
    TEARDOWN = "teardown"


class FakeReport:
    def __init__(self, when, outcome, longrepr=None):
        self.when = when
        self.outcome = outcome
        self.longrepr = longrepr
        self.user_properties = []


def get_user_property(report, key):
    for k, v in report.user_properties:
        if k == key:
            return v
    return None


# This mirrors RetryReports.log_test_report verbatim.
def log_test_report(reports_by_outcome, reports, when):
    report = reports.get(when)
    if report is None:
        return
    outcome = get_user_property(report, "dd_retry_outcome") or report.outcome
    reports_by_outcome[outcome].append(report)


# This mirrors _mark_test_reports_as_retry: marks call, falls back to setup.
def mark_as_retry(reports):
    for when in (TestPhase.CALL, TestPhase.SETUP):
        if when in reports:
            r = reports[when]
            r.user_properties.append(("dd_retry_outcome", r.outcome))
            r.outcome = "rerun"
            return


# This mirrors _get_test_outcome: first failed/skipped phase wins.
def get_test_run_status(reports):
    for phase in (TestPhase.SETUP, TestPhase.CALL, TestPhase.TEARDOWN):
        r = reports.get(phase)
        if r is None:
            continue
        if r.outcome == "failed":
            return TestStatus.FAIL
        if r.outcome == "skipped":
            return TestStatus.SKIP
    return TestStatus.PASS


# === the proof ===
def main():
    # The scenario: setup passes, call passes, teardown fails.
    # Plausible cause for the kafka tests: consumer.close() or tracer.flush()
    # erroring during fixture teardown.
    reports = {
        TestPhase.SETUP: FakeReport(when="setup", outcome="passed"),
        TestPhase.CALL: FakeReport(when="call", outcome="passed"),
        TestPhase.TEARDOWN: FakeReport(
            when="teardown", outcome="failed", longrepr="example teardown exception"
        ),
    }

    # System A: test_run.status (used by retry handlers to decide pass/fail)
    test_run_status = get_test_run_status(reports)

    # System B: reports_by_outcome (used by make_final_report to find a
    # source report for longrepr/wasxfail).
    reports_by_outcome = defaultdict(list)
    mark_as_retry(reports)
    log_test_report(reports_by_outcome, reports, TestPhase.SETUP)
    log_test_report(reports_by_outcome, reports, TestPhase.CALL)

    print(f"test_run.status (System A, considers all phases): {test_run_status}")
    print(f"reports_by_outcome  (System B, considers setup+call only):")
    for k, v in reports_by_outcome.items():
        print(f"  {k!r}: {len(v)} report(s)")

    failed_reports = reports_by_outcome.get("failed", [])
    print()
    print(f"System A says the test FAILED. System B has {len(failed_reports)} 'failed' reports.")
    print()
    if test_run_status == TestStatus.FAIL and len(failed_reports) == 0:
        print("BUG REPRODUCED: the two systems disagree by construction.")
        print("make_final_report(final_status=FAIL) would IndexError on reports_by_outcome['failed'][0],")
        print("fall back to longrepr=None, and crash junitxml.")
    else:
        raise AssertionError("expected the mismatch but didn't see it")


if __name__ == "__main__":
    main()
