"""Fake test runner that uses EFD where some of the test retries fail, but the test passes

Incorporates setting and deleting tags, as well.
Starts session before discovery (simulating pytest behavior)

Comment lines in the test start/finish lines are there for visual distinction.
"""
import json
from multiprocessing import freeze_support
from pathlib import Path
from unittest import mock

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.ext.test_visibility.api import TestStatus
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.test_visibility import api
from ddtrace.internal.test_visibility._efd_mixins import EFDTestStatus


def _hack_test_duration(test_id: api.InternalTestId, duration: float):
    from ddtrace.internal.ci_visibility import CIVisibility

    test = CIVisibility.get_test_by_id(test_id)
    test._span.start_ns = test._span.start_ns - int(duration * 1e9)


def _make_test_ids():
    _known_test_ids = {
        "m1": {
            "m1_s1": [
                ["m1_s1_t3"],
                ["m1_s1_t4_p1", "params_to_ignore_1"],
                ["m1_s1_t4_p2", "params_to_ignore_2"],
            ]
        },
        "m2": {
            "m2_s1": [
                ["m2_s1_t1"],
                ["m2_s1_t2"],
                ["m2_s1_t3"],
                ["m2_s1_t4"],
                ["m2_s1_t5"],
                ["m2_s1_t6"],
                ["m2_s1_t7"],
                ["m2_s1_t8"],
                ["m2_s1_t9"],
            ],
            "m2_s2": [
                ["m2_s2_t1"],
                ["m2_s2_t4"],
                ["m2_s2_t5"],
            ],
        },
    }

    test_ids = set()
    for module, suites in _known_test_ids.items():
        module_id = ext_api.TestModuleId(module)
        for suite, tests in suites.items():
            suite_id = ext_api.TestSuiteId(module_id, suite)
            for test in tests:
                test_id = api.InternalTestId(suite_id, *test)
                test_ids.add(test_id)
    return test_ids


def run_tests():
    # START DISCOVERY
    api.InternalTestSession.discover("manual_efd_mix_pass", "dd_manual_test_fw", "1.0.0")
    api.InternalTestSession.start()

    # M1

    m1_id = ext_api.TestModuleId("m1")

    api.InternalTestModule.discover(m1_id)

    # M1_S1

    m1_s1_id = ext_api.TestSuiteId(m1_id, "m1_s1")
    api.InternalTestSuite.discover(m1_s1_id)

    # M1_S1 tests

    m1_s1_t1_id = api.InternalTestId(m1_s1_id, "m1_s1_t1")
    api.InternalTest.discover(m1_s1_t1_id, source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 1, 2))

    m1_s1_t2_id = api.InternalTestId(m1_s1_id, "m1_s1_t2")
    api.InternalTest.discover(m1_s1_t2_id, source_file_info=None)

    m1_s1_t3_id = api.InternalTestId(m1_s1_id, "m1_s1_t3")
    api.InternalTest.discover(
        m1_s1_t3_id,
        codeowners=["@romain", "@romain2"],
        source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 4, 12),
    )

    # NOTE: these parametrized tests will not be retried
    m1_s1_t4_p1_id = api.InternalTestId(m1_s1_id, "m1_s1_t4_p1", parameters=json.dumps({"param1": "value1"}))
    api.InternalTest.discover(m1_s1_t4_p1_id)
    m1_s1_t4_p2_id = api.InternalTestId(m1_s1_id, "m1_s1_t4_p2_id", parameters=json.dumps({"param1": "value2"}))
    api.InternalTest.discover(m1_s1_t4_p2_id)

    # M2

    m2_id = ext_api.TestModuleId("m2")
    api.InternalTestModule.discover(m2_id)

    # M2_S1

    m2_s1_id = ext_api.TestSuiteId(m2_id, "m2_s1")
    api.InternalTestSuite.discover(m2_s1_id)

    # M2_S1 tests (mostly exist to keep under faulty session threshold)
    m2_s1_test_ids = [
        api.InternalTestId(m2_s1_id, "m2_s1_t1"),
        api.InternalTestId(m2_s1_id, "m2_s1_t2"),
        api.InternalTestId(m2_s1_id, "m2_s1_t3"),
        api.InternalTestId(m2_s1_id, "m2_s1_t4"),
        api.InternalTestId(m2_s1_id, "m2_s1_t5"),
        api.InternalTestId(m2_s1_id, "m2_s1_t6"),
        api.InternalTestId(m2_s1_id, "m2_s1_t7"),
        api.InternalTestId(m2_s1_id, "m2_s1_t8"),
        api.InternalTestId(m2_s1_id, "m2_s1_t9"),
    ]
    for test_id in m2_s1_test_ids:
        api.InternalTest.discover(test_id)

    # M2_S2

    m2_s2_id = ext_api.TestSuiteId(m2_id, "m2_s2")
    api.InternalTestSuite.discover(m2_s2_id)

    # M2_S2 tests

    m2_s2_t1_id = api.InternalTestId(m2_s2_id, "m2_s2_t1")
    api.InternalTest.discover(m2_s2_t1_id, source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 1, 2))

    m2_s2_t2_id = api.InternalTestId(m2_s2_id, "m2_s2_t2")
    api.InternalTest.discover(m2_s2_t2_id)

    m2_s2_t3_id = api.InternalTestId(m2_s2_id, "m2_s2_t3")
    api.InternalTest.discover(
        m2_s2_t3_id,
        codeowners=["@romain"],
        source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 4, 12),
    )

    m2_s2_t4_id = api.InternalTestId(m2_s2_id, "m2_s2_t4")
    api.InternalTest.discover(m2_s2_t4_id)

    m2_s2_t5_id = api.InternalTestId(m2_s2_id, "m2_s2_t5")
    api.InternalTest.discover(m2_s2_t5_id)

    # END DISCOVERY

    # START TESTS

    assert not api.InternalTestSession.efd_is_faulty_session(), "Session is faulty but should not be"

    # START M1

    api.InternalTestModule.start(m1_id)

    # START M1_S1

    api.InternalTestSuite.start(m1_s1_id)

    # m1_s1_t1 test expect 10 EFD retries
    api.InternalTest.start(m1_s1_t1_id)
    _hack_test_duration(m1_s1_t1_id, 1.23456)
    api.InternalTest.finish(m1_s1_t1_id, TestStatus.PASS)
    # Hack duration:

    m1_s1_t1_retry_count = 0
    while api.InternalTest.efd_should_retry(m1_s1_t1_id):
        m1_s1_t1_retry_count += 1
        m1_s1_t1_retry_number = api.InternalTest.efd_add_retry(m1_s1_t1_id, start_immediately=True)
        api.InternalTest.efd_finish_retry(
            m1_s1_t1_id, m1_s1_t1_retry_number, TestStatus.PASS if m1_s1_t1_retry_count % 2 == 0 else TestStatus.FAIL
        )
    assert m1_s1_t1_retry_count == 10, "Expected 10 EFD retries, got %s" % m1_s1_t1_retry_count
    m1_s1_t1_final_status = api.InternalTest.efd_get_final_status(m1_s1_t1_id)
    assert m1_s1_t1_final_status == EFDTestStatus.FLAKY, (
        "Expected final status to be FLAKY, got %s" % m1_s1_t1_final_status
    )

    # m1_s1_t2 test: expect 5 EFD retries
    api.InternalTest.start(m1_s1_t2_id)
    _hack_test_duration(m1_s1_t2_id, 6.54321)
    api.InternalTest.mark_pass(m1_s1_t2_id)

    m1_s1_t2_retry_count = 0
    while api.InternalTest.efd_should_retry(m1_s1_t2_id):
        m1_s1_t2_retry_count += 1
        m1_s1_t2_retry_number = api.InternalTest.efd_add_retry(m1_s1_t2_id, start_immediately=True)
        api.InternalTest.efd_finish_retry(
            m1_s1_t2_id, m1_s1_t2_retry_number, TestStatus.PASS if m1_s1_t2_retry_count > 2 else TestStatus.FAIL
        )
    assert m1_s1_t2_retry_count == 5, "Expected 5 EFD retries, got %s" % m1_s1_t2_retry_count
    m1_s1_t2_final_status = api.InternalTest.efd_get_final_status(m1_s1_t2_id)
    assert m1_s1_t2_final_status == EFDTestStatus.FLAKY, (
        "Expected final status to be FLAKY, got %s" % m1_s1_t2_final_status
    )

    # m1_s1_t3 test: expect no retries (is a known test)
    api.InternalTest.start(m1_s1_t3_id)
    assert not api.InternalTest.efd_should_retry(m1_s1_t3_id), "Should not retry: known"
    api.InternalTest.mark_skip(m1_s1_t3_id)

    # m1_s1_t4 parametrized tests and EFD retries (but it should not retry parametrized tests)
    api.InternalTest.start(m1_s1_t4_p1_id)
    assert not api.InternalTest.efd_should_retry(m1_s1_t4_p1_id), "Should not retry: parametrized"
    api.InternalTest.mark_skip(m1_s1_t4_p1_id)
    api.InternalTest.start(m1_s1_t4_p2_id)
    assert not api.InternalTest.efd_should_retry(m1_s1_t4_p2_id), "Should not retry: parametrized"
    api.InternalTest.mark_pass(m1_s1_t4_p2_id)

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
        assert not api.InternalTest.efd_should_retry(test_id), "Should not retry: known"
        api.InternalTest.mark_pass(test_id)

    api.InternalTestSuite.finish(m2_s1_id)

    # END M2_S1

    # START M2_S2

    api.InternalTestSuite.start(m2_s2_id)

    # should not be retried (known test)
    api.InternalTest.start(m2_s2_t1_id)
    assert not api.InternalTest.efd_should_retry(m2_s2_t1_id), "Should not retry: known"
    api.InternalTest.mark_skip(m2_s2_t1_id)

    # should not be retried (over max duration)
    api.InternalTest.start(m2_s2_t2_id)
    _hack_test_duration(m2_s2_t2_id, 301.0)
    api.InternalTest.mark_pass(m2_s2_t2_id)
    assert not api.InternalTest.efd_should_retry(m2_s2_t2_id), "Should not retry: over max duration"

    # should not be retried (duration not recorded)
    api.InternalTest.start(m2_s2_t3_id)
    assert not api.InternalTest.efd_should_retry(m2_s2_t3_id), "Should not retry: initial status not recorded"
    api.InternalTest.mark_pass(m2_s2_t3_id)

    # no EFD retries expected (known test)
    api.InternalTest.start(m2_s2_t4_id)
    assert not api.InternalTest.efd_should_retry(m2_s2_t4_id), "Should not retry: known"
    api.InternalTest.mark_pass(m2_s2_t4_id)

    # test: no EFD retries expected (known test)
    api.InternalTest.start(m2_s2_t5_id)
    assert not api.InternalTest.efd_should_retry(m2_s2_t5_id), "Should not retry: known"
    api.InternalTest.mark_pass(m2_s2_t5_id)

    api.InternalTestSuite.finish(m2_s2_id)

    api.InternalTestModule.finish(m2_id)

    api.InternalTestSession.finish()

    # FINISH TESTS


def main():
    with mock.patch(
        "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
        return_value=TestVisibilityAPISettings(
            require_git=False, early_flake_detection=EarlyFlakeDetectionSettings(True)
        ),
    ):
        ext_api.enable_test_visibility()

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._instance._known_test_ids",
            _make_test_ids(),
        ):
            run_tests()

        ext_api.disable_test_visibility()


if __name__ == "__main__":
    freeze_support()
    main()
