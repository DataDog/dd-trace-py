"""Fake test runner that uses ATR where some of the test retries fail, but the test passes

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
from ddtrace.internal.test_visibility.api import InternalTest


def run_tests():
    # START DISCOVERY
    api.InternalTestSession.discover("manual_atr_mix_pass", "dd_manual_test_fw", "1.0.0")
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

    # NOTE: these parametrized tests will not be retried
    m1_s1_t4_p1_id = ext_api.TestId(m1_s1_id, "m1_s1_t4_p1", parameters=json.dumps({"param1": "value1"}))
    api.InternalTest.discover(m1_s1_t4_p1_id)
    m1_s1_t4_p2_id = ext_api.TestId(m1_s1_id, "m1_s1_t4_p2_id", parameters=json.dumps({"param1": "value2"}))
    api.InternalTest.discover(m1_s1_t4_p2_id)

    # M2

    m2_id = ext_api.TestModuleId("m2")
    api.InternalTestModule.discover(m2_id)

    # M2_S1
    m2_s1_id = ext_api.TestSuiteId(m2_id, "m2_s1")
    api.InternalTestSuite.discover(m2_s1_id)

    # M2_S1 tests

    m2_s1_t1_id = ext_api.TestId(m2_s1_id, "m2_s1_t1")
    api.InternalTest.discover(m2_s1_t1_id, source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 1, 2))

    m2_s1_t2_id = ext_api.TestId(m2_s1_id, "m2_s1_t2")
    api.InternalTest.discover(m2_s1_t2_id)

    m2_s1_t3_id = ext_api.TestId(m2_s1_id, "m2_s1_t3")
    api.InternalTest.discover(
        m2_s1_t3_id,
        codeowners=["@romain"],
        source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 4, 12),
    )

    # END DISCOVERY

    # START TESTS

    # START M1

    api.InternalTestModule.start(m1_id)

    # START M1_S1

    api.InternalTestSuite.start(m1_s1_id)

    # m1_s1_t1 test expect to pass on the 4th retry
    api.InternalTest.start(m1_s1_t1_id)
    api.InternalTest.finish(m1_s1_t1_id, TestStatus.FAIL)

    m1_s1_t1_retry_count = 0
    while api.InternalTest.atr_should_retry(m1_s1_t1_id):
        m1_s1_t1_retry_count += 1
        m1_s1_t1_retry_number = api.InternalTest.atr_add_retry(m1_s1_t1_id, start_immediately=True)
        api.InternalTest.atr_finish_retry(
            m1_s1_t1_id, m1_s1_t1_retry_number, TestStatus.PASS if m1_s1_t1_retry_count % 4 == 0 else TestStatus.FAIL
        )
    assert m1_s1_t1_retry_count == 4, "Expected 4 ATR retries, got %s" % m1_s1_t1_retry_count
    m1_s1_t1_final_status = api.InternalTest.atr_get_final_status(m1_s1_t1_id)
    assert m1_s1_t1_final_status == TestStatus.PASS, "Expected final status to be PASS, got %s" % m1_s1_t1_final_status

    # m1_s1_t2 test: expect to pass on the 2nd retry
    api.InternalTest.start(m1_s1_t2_id)
    api.InternalTest.mark_fail(m1_s1_t2_id)

    m1_s1_t2_retry_count = 0
    while api.InternalTest.atr_should_retry(m1_s1_t2_id):
        m1_s1_t2_retry_count += 1
        m1_s1_t2_retry_number = api.InternalTest.atr_add_retry(m1_s1_t2_id, start_immediately=True)
        api.InternalTest.atr_finish_retry(
            m1_s1_t2_id, m1_s1_t2_retry_number, TestStatus.PASS if m1_s1_t2_retry_count > 1 else TestStatus.FAIL
        )
    assert m1_s1_t2_retry_count == 2, "Expected 2 ATR retries, got %s" % m1_s1_t2_retry_count
    m1_s1_t2_final_status = api.InternalTest.atr_get_final_status(m1_s1_t2_id)
    assert m1_s1_t2_final_status == TestStatus.PASS, "Expected final status to be PASS, got %s" % m1_s1_t2_final_status

    # m1_s1_t3 test: expect no retries (it passed on the first try)
    api.InternalTest.start(m1_s1_t3_id)
    api.InternalTest.mark_pass(m1_s1_t3_id)
    assert not api.InternalTest.atr_should_retry(m1_s1_t3_id), "Should not retry: passed first attempt"

    # m1_s1_t4 parametrized tests and ATR retries (but they pass or skip and should not retry)
    api.InternalTest.start(m1_s1_t4_p1_id)
    api.InternalTest.mark_skip(m1_s1_t4_p1_id)
    assert not api.InternalTest.atr_should_retry(m1_s1_t4_p1_id), "Should not retry: skipped first attempt"
    api.InternalTest.start(m1_s1_t4_p2_id)
    api.InternalTest.mark_pass(m1_s1_t4_p2_id)
    assert not api.InternalTest.atr_should_retry(m1_s1_t4_p2_id), "Should not retry: passed first attempt"

    api.InternalTestSuite.finish(m1_s1_id)

    # END M1_S1

    api.InternalTestModule.finish(m1_id)

    # END M1

    # START M2

    api.InternalTestModule.start(m2_id)

    # START M2_S1

    api.InternalTestSuite.start(m2_s1_id)

    # should not be retried (skipped)
    api.InternalTest.start(m2_s1_t1_id)
    api.InternalTest.mark_skip(m2_s1_t1_id)
    assert not api.InternalTest.atr_should_retry(m2_s1_t1_id), "Should not retry: skipped"

    # expect to pass on 5th retry, and skips every other
    api.InternalTest.start(m2_s1_t2_id)
    api.InternalTest.mark_fail(m2_s1_t2_id)
    m2_s1_t2_retry_count = 0
    while InternalTest.atr_should_retry(m2_s1_t2_id):
        m2_s1_t2_retry_count += 1
        m2_s1_t2_retry_number = InternalTest.atr_add_retry(m2_s1_t2_id, start_immediately=True)
        InternalTest.atr_finish_retry(
            m2_s1_t2_id, m2_s1_t2_retry_number, TestStatus.PASS if m2_s1_t2_retry_count == 5 else TestStatus.SKIP
        )
    assert m2_s1_t2_retry_count == 5, "Expected 5 ATR retries, got %s" % m2_s1_t2_retry_count
    m2_s1_t2_final_status = InternalTest.atr_get_final_status(m2_s1_t2_id)
    assert m2_s1_t2_final_status == TestStatus.PASS, "Expected final status to be PASS, got %s" % m2_s1_t2_final_status

    # should not be retried (max session retry count exceeded)
    api.InternalTest.start(m2_s1_t3_id)
    api.InternalTest.mark_pass(m2_s1_t3_id)
    assert not api.InternalTest.atr_should_retry(m2_s1_t3_id), "Should not retry: passed first attempt"

    api.InternalTestSuite.finish(m2_s1_id)

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
