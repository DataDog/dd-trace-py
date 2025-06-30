"""Fake test runner that uses ATR where retried tests fail

Also:
- tests that setting a custom retry count is respected, so must be invoked with DD_CIVISIBILITY_FLAKY_RETRY_COUNT=7
- tests that max session retry count is respected, so must be invoked with DD_CIVISIBILITY_TOTAL_FLAKY_RETRY_COUNT=20

Comment lines in the test start/finish lines are there for visual distinction.
"""
import json
from multiprocessing import freeze_support
from pathlib import Path
from unittest import mock

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.test_visibility import api


def run_tests():
    # START DISCOVERY
    api.InternalTestSession.discover("manual_atr_mix_fail", "dd_manual_test_fw", "1.0.0")
    api.InternalTestSession.start()

    # M1

    m1_id = ext_api.TestModuleId("m1")

    api.InternalTestModule.discover(m1_id)

    # M1_S1

    m1_s1_id = ext_api.TestSuiteId(m1_id, "m1_s1")
    api.InternalTestSuite.discover(m1_s1_id)

    # M1_S1 tests

    m1_s1_t1_id = ext_api.TestId(m1_s1_id, "m1_s1_t1")
    api.InternalTest.discover(m1_s1_t1_id, source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 1, 2))

    m1_s1_t2_id = ext_api.TestId(m1_s1_id, "m1_s1_t2")
    api.InternalTest.discover(m1_s1_t2_id, source_file_info=None)

    m1_s1_t3_id = ext_api.TestId(m1_s1_id, "m1_s1_t3")
    api.InternalTest.discover(
        m1_s1_t3_id,
        codeowners=["@romain", "@romain2"],
        source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 4, 12),
    )

    m1_s1_t4_p1_id = ext_api.TestId(m1_s1_id, "m1_s1_t4_p1", parameters=json.dumps({"param1": "value1"}))
    api.InternalTest.discover(m1_s1_t4_p1_id)
    m1_s1_t4_p2_id = ext_api.TestId(m1_s1_id, "m1_s1_t4_p2_id", parameters=json.dumps({"param1": "value2"}))
    api.InternalTest.discover(m1_s1_t4_p2_id)
    m1_s1_t4_p3_id = ext_api.TestId(m1_s1_id, "m1_s1_t4_p3_id", parameters=json.dumps({"param1": "value3"}))
    api.InternalTest.discover(m1_s1_t4_p3_id)

    # M2

    m2_id = ext_api.TestModuleId("m2")
    api.InternalTestModule.discover(m2_id)

    # M2_S1

    m2_s1_id = ext_api.TestSuiteId(m2_id, "m2_s1")
    api.InternalTestSuite.discover(m2_s1_id)

    # M2_S1 tests all pass
    m2_s1_test_ids = [
        ext_api.TestId(m2_s1_id, "m2_s1_t1"),
        ext_api.TestId(m2_s1_id, "m2_s1_t2"),
        ext_api.TestId(m2_s1_id, "m2_s1_t3"),
        ext_api.TestId(m2_s1_id, "m2_s1_t4"),
        ext_api.TestId(m2_s1_id, "m2_s1_t5"),
        ext_api.TestId(m2_s1_id, "m2_s1_t6"),
        ext_api.TestId(m2_s1_id, "m2_s1_t7"),
        ext_api.TestId(m2_s1_id, "m2_s1_t8"),
        ext_api.TestId(m2_s1_id, "m2_s1_t9"),
    ]
    for test_id in m2_s1_test_ids:
        api.InternalTest.discover(test_id)

    # M2_S2

    m2_s2_id = ext_api.TestSuiteId(m2_id, "m2_s2")
    api.InternalTestSuite.discover(m2_s2_id)

    # M2_S2 tests

    m2_s2_t1_id = ext_api.TestId(m2_s2_id, "m2_s2_t1")
    api.InternalTest.discover(m2_s2_t1_id, source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 1, 2))

    m2_s2_t2_id = ext_api.TestId(m2_s2_id, "m2_s2_t2")
    api.InternalTest.discover(m2_s2_t2_id)

    m2_s2_t3_id = ext_api.TestId(m2_s2_id, "m2_s2_t3")
    api.InternalTest.discover(
        m2_s2_t3_id,
        codeowners=["@romain"],
        source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 4, 12),
    )

    # END DISCOVERY

    # START TESTS

    # START M1

    api.InternalTestModule.start(m1_id)

    # START M1_S1

    api.InternalTestSuite.start(m1_s1_id)

    # m1_s1_t1 test expect 7 ATR retries
    api.InternalTest.start(m1_s1_t1_id)
    api.InternalTest.finish(m1_s1_t1_id, TestStatus.FAIL)

    m1_s1_t1_retry_count = 0
    while api.InternalTest.atr_should_retry(m1_s1_t1_id):
        m1_s1_t1_retry_count += 1
        m1_s1_t1_retry_number = api.InternalTest.atr_add_retry(m1_s1_t1_id, start_immediately=True)
        api.InternalTest.atr_finish_retry(m1_s1_t1_id, m1_s1_t1_retry_number, TestStatus.FAIL)
    assert m1_s1_t1_retry_count == 7, "Expected 7 ATR retries, got %s" % m1_s1_t1_retry_count
    m1_s1_t1_final_status = api.InternalTest.atr_get_final_status(m1_s1_t1_id)
    assert m1_s1_t1_final_status == TestStatus.FAIL, "Expected final status to be FAIL, got %s" % m1_s1_t1_final_status

    # m1_s1_t2 test: expect 7 ATR retries, passes on last attempt
    api.InternalTest.start(m1_s1_t2_id)
    api.InternalTest.finish(m1_s1_t2_id, TestStatus.FAIL)

    m1_s1_t2_retry_count = 0
    while api.InternalTest.atr_should_retry(m1_s1_t2_id):
        m1_s1_t2_retry_count += 1
        m1_s1_t2_retry_number = api.InternalTest.atr_add_retry(m1_s1_t2_id, start_immediately=True)
        api.InternalTest.atr_finish_retry(
            m1_s1_t2_id, m1_s1_t2_retry_number, TestStatus.PASS if m1_s1_t2_retry_count == 7 else TestStatus.FAIL
        )
    assert m1_s1_t2_retry_count == 7, "Expected 7 ATR retries, got %s" % m1_s1_t2_retry_count
    m1_s1_t2_final_status = api.InternalTest.atr_get_final_status(m1_s1_t2_id)
    assert m1_s1_t2_final_status == TestStatus.PASS, "Expected final status to be PASS, got %s" % m1_s1_t2_final_status

    # m1_s1_t3 test: expect no retries (skipped)
    api.InternalTest.start(m1_s1_t3_id)
    api.InternalTest.mark_skip(m1_s1_t3_id)
    assert not api.InternalTest.atr_should_retry(m1_s1_t3_id), "Should not retry: first attempt skipped"

    # Parametrized tests should only be retried if the first status fails
    api.InternalTest.start(m1_s1_t4_p1_id)
    api.InternalTest.finish(m1_s1_t4_p1_id, TestStatus.PASS)
    assert not api.InternalTest.atr_should_retry(m1_s1_t4_p1_id), "Should not retry: first attempt passed"

    api.InternalTest.start(m1_s1_t4_p2_id)
    api.InternalTest.mark_fail(m1_s1_t4_p2_id)
    m1_s1_t4_p2_retry_count = 0
    while api.InternalTest.atr_should_retry(m1_s1_t4_p2_id):
        m1_s1_t4_p2_retry_count += 1
        m1_s1_t4_p2_retry_number = api.InternalTest.atr_add_retry(m1_s1_t4_p2_id, start_immediately=True)
        api.InternalTest.atr_finish_retry(
            m1_s1_t4_p2_id,
            m1_s1_t4_p2_retry_number,
            TestStatus.PASS if m1_s1_t4_p2_retry_count > 4 else TestStatus.FAIL,
        )
    assert m1_s1_t4_p2_retry_count == 5, "Expected 5 ATR retries, got %s" % m1_s1_t4_p2_retry_count
    m1_s1_t4_p2_final_status = api.InternalTest.atr_get_final_status(m1_s1_t4_p2_id)
    assert m1_s1_t4_p2_final_status == TestStatus.PASS, (
        "Expected final status to be PASS, got %s" % m1_s1_t4_p2_final_status
    )

    api.InternalTest.start(m1_s1_t4_p3_id)
    api.InternalTest.mark_skip(m1_s1_t4_p3_id)
    assert not api.InternalTest.atr_should_retry(m1_s1_t4_p3_id), "Should not retry: first attempt skipped"

    api.InternalTestSuite.finish(m1_s1_id)

    # END M1_S1

    api.InternalTestModule.finish(m1_id)

    # END M1

    # START M2

    api.InternalTestModule.start(m2_id)

    # START M2_S1

    api.InternalTestSuite.start(m2_s1_id)

    for test_id in m2_s1_test_ids:
        api.InternalTest.start(test_id)
        api.InternalTest.mark_pass(test_id)
        assert not api.InternalTest.atr_should_retry(test_id), "Should not retry: passed first attempt"

    api.InternalTestSuite.finish(m2_s1_id)

    # END M2_S1

    # START M2_S2

    api.InternalTestSuite.start(m2_s2_id)

    # should not be retried (skipped)
    api.InternalTest.start(m2_s2_t1_id)
    api.InternalTest.mark_skip(m2_s2_t1_id)
    assert not api.InternalTest.atr_should_retry(m2_s2_t1_id), "Should not retry: skipped first attempt"

    # m2_s2_t2 test: expects 1 retries, then fail because total session reries max is reached
    api.InternalTest.start(m2_s2_t2_id)
    api.InternalTest.finish(m2_s2_t2_id, TestStatus.FAIL)

    m2_s2_t2_retry_count = 0
    while api.InternalTest.atr_should_retry(m2_s2_t2_id):
        m2_s2_t2_retry_count += 1
        m2_s2_t2_retry_number = api.InternalTest.atr_add_retry(m2_s2_t2_id, start_immediately=True)
        api.InternalTest.atr_finish_retry(m2_s2_t2_id, m2_s2_t2_retry_number, TestStatus.FAIL)
    assert m2_s2_t2_retry_count == 1, "Expected 1 ATR retries, got %s" % m2_s2_t2_retry_count
    m2_s2_t2_final_status = api.InternalTest.atr_get_final_status(m2_s2_t2_id)
    assert m2_s2_t2_final_status == TestStatus.FAIL, "Expected final status to be FAIL, got %s" % m2_s2_t2_final_status

    # should not be retried (passed)
    api.InternalTest.start(m2_s2_t3_id)
    api.InternalTest.mark_pass(m2_s2_t3_id)
    assert not api.InternalTest.atr_should_retry(m2_s2_t3_id), "Should not retry: passed first attempt"

    api.InternalTestSuite.finish(m2_s2_id)

    api.InternalTestModule.finish(m2_id)

    api.InternalTestSession.finish()

    # FINISH TESTS


def main():
    with mock.patch(
        "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
        return_value=TestVisibilityAPISettings(require_git=False, flaky_test_retries_enabled=True),
    ):
        ext_api.enable_test_visibility()

        run_tests()

        ext_api.disable_test_visibility()


if __name__ == "__main__":
    freeze_support()
    main()
