"""Fake test runner where all too many tests are new, so the session is faulty and no retries are done

Incorporates setting and deleting tags, as well.
Starts session before discovery (simulating pytest behavior)

Comment lines in the test start/finish lines are there for visual distinction.
"""
import json
from multiprocessing import freeze_support
from pathlib import Path
from unittest import mock

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.test_visibility import api


def _make_test_ids():
    _unique_test_ids = {
        "m1": {
            "m1_s1": [
                ["m1_s1_t3"],
                ["m1_s1_t4_p1", "params_to_ignore_1"],
            ]
        },
        "m2": {
            "m2_s2": [
                ["m2_s2_t1"],
                ["m2_s2_t5"],
            ],
        },
    }

    test_ids = set()
    for module, suites in _unique_test_ids.items():
        module_id = ext_api.TestModuleId(module)
        for suite, tests in suites.items():
            suite_id = ext_api.TestSuiteId(module_id, suite)
            for test in tests:
                test_id = api.InternalTestId(suite_id, *test)
                test_ids.add(test_id)
    return test_ids


def run_tests():
    # START DISCOVERY
    api.InternalTestSession.discover("manual_efd_faulty_session", "dd_manual_test_fw", "1.0.0")
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

    assert api.InternalTestSession.efd_is_faulty_session(), "Session should be faulty"

    # START M1

    api.InternalTestModule.start(m1_id)

    # START M1_S1

    api.InternalTestSuite.start(m1_s1_id)

    api.InternalTest.start(m1_s1_t1_id)
    api.InternalTest.mark_pass(m1_s1_t1_id)
    assert not api.InternalTest.efd_should_retry(m1_s1_t1_id), "Should not retry: session is faulty"
    api.InternalTest.mark_pass(m1_s1_t1_id)

    api.InternalTest.start(m1_s1_t2_id)
    api.InternalTest.mark_pass(m1_s1_t2_id)
    assert not api.InternalTest.efd_should_retry(m1_s1_t2_id), "Should not retry: session is faulty"
    api.InternalTest.mark_pass(m1_s1_t2_id)

    api.InternalTest.start(m1_s1_t3_id)
    assert not api.InternalTest.efd_should_retry(m1_s1_t3_id), "Should not retry: session is faulty"
    api.InternalTest.mark_skip(m1_s1_t3_id)

    api.InternalTest.start(m1_s1_t4_p1_id)
    assert not api.InternalTest.efd_should_retry(m1_s1_t4_p1_id), "Should not retry: session is faulty"
    api.InternalTest.mark_skip(m1_s1_t4_p1_id)
    api.InternalTest.start(m1_s1_t4_p2_id)
    assert not api.InternalTest.efd_should_retry(m1_s1_t4_p2_id), "Should not retry: session is faulty"
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
        assert not api.InternalTest.efd_should_retry(test_id), "Should not retry: session is faulty"
        api.InternalTest.mark_pass(test_id)

    api.InternalTestSuite.finish(m2_s1_id)

    # END M2_S1

    # START M2_S2

    api.InternalTestSuite.start(m2_s2_id)

    api.InternalTest.start(m2_s2_t1_id)
    assert not api.InternalTest.efd_should_retry(m2_s2_t1_id), "Should not retry: session is faulty"
    api.InternalTest.mark_skip(m2_s2_t1_id)

    api.InternalTest.start(m2_s2_t2_id)
    api.InternalTest.mark_pass(m2_s2_t2_id)
    assert not api.InternalTest.efd_should_retry(m2_s2_t2_id), "Should not retry: session is faulty"
    api.InternalTest.mark_pass(m2_s2_t2_id)

    api.InternalTest.start(m2_s2_t3_id)
    assert not api.InternalTest.efd_should_retry(m2_s2_t3_id), "Should not retry: session is faulty"
    api.InternalTest.mark_pass(m2_s2_t3_id)

    api.InternalTest.start(m2_s2_t4_id)
    assert not api.InternalTest.efd_should_retry(m2_s2_t4_id), "Should not retry: session is faulty"
    api.InternalTest.mark_pass(m2_s2_t4_id)

    api.InternalTest.start(m2_s2_t5_id)
    assert not api.InternalTest.efd_should_retry(m2_s2_t5_id), "Should not retry: session is faulty"
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
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._instance._unique_test_ids",
            _make_test_ids(),
        ):
            run_tests()

        ext_api.disable_test_visibility()


if __name__ == "__main__":
    freeze_support()
    main()
