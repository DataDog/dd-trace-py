"""Fake test runner where tests have a mix of fail, skip or pass, and the final session fails

Incorporates setting and deleting tags, as well.
Starts session before discovery (simulating pytest behavior)

Module 1 should fail, suite 1 should fail
Module 2 should pass, suite 2 should pass
Module 3 should fail, suite 3 should fail but suite 4 should pass
Module 4 should pass, but be marked forced run, and suite 6 should be marked
as forced run and unskippable

Entire session should be marked as forced run

ITR coverage data is added at the suite level. suite 1 adds coverage after individual tests.
Suite 2 adds coverage at the end of the suite. Suite 3 mixes the two. Suite 4 has no coverage.

Comment lines in the test start/finish lines are there for visual distinction.
"""
import json
from multiprocessing import freeze_support
from pathlib import Path
import sys
from unittest import mock

from ddtrace.ext.ci_visibility import api
from ddtrace.internal.ci_visibility.utils import take_over_logger_stream_handler


def _make_excinfo():
    try:
        raise ValueError("This is a fake exception")
    except ValueError:
        return api.CIExcInfo(*sys.exc_info())


def main():
    take_over_logger_stream_handler()

    api.enable_ci_visibility()

    # START DISCOVERY
    api.CISession.discover("manual_test_mix_fail_itr_suite_level", "dd_manual_test_fw", "1.0.0")
    api.CISession.start()

    module_1_id = api.CIModuleId("module_1")

    api.CIModule.discover(module_1_id)

    suite_1_id = api.CISuiteId(module_1_id, "suite_1")
    api.CISuite.discover(suite_1_id)

    suite_1_test_1_id = api.CITestId(suite_1_id, "test_1")
    suite_1_test_2_id = api.CITestId(suite_1_id, "test_2")
    suite_1_test_3_id = api.CITestId(suite_1_id, "test_3")
    suite_1_test_3_retry_1_id = api.CITestId(suite_1_id, "test_3", retry_number=1)
    suite_1_test_3_retry_2_id = api.CITestId(suite_1_id, "test_3", retry_number=2)
    suite_1_test_3_retry_3_id = api.CITestId(suite_1_id, "test_3", retry_number=3)

    suite_1_test_4_parametrized_1_id = api.CITestId(suite_1_id, "test_4", parameters=json.dumps({"param1": "value1"}))
    suite_1_test_4_parametrized_2_id = api.CITestId(suite_1_id, "test_4", parameters=json.dumps({"param1": "value2"}))
    suite_1_test_4_parametrized_3_id = api.CITestId(suite_1_id, "test_4", parameters=json.dumps({"param1": "value3"}))

    api.CITest.discover(suite_1_test_1_id, source_file_info=api.CISourceFileInfo(Path("my_file_1.py"), 1, 2))
    api.CITest.discover(suite_1_test_2_id, source_file_info=None)
    api.CITest.discover(
        suite_1_test_3_id,
        codeowners=["@romain", "@romain2"],
        source_file_info=api.CISourceFileInfo(Path("my_file_1.py"), 4, 12),
        is_early_flake_detection=True,
    )
    api.CITest.discover_early_flake_retry(suite_1_test_3_retry_1_id)
    api.CITest.discover_early_flake_retry(suite_1_test_3_retry_2_id)
    api.CITest.discover_early_flake_retry(suite_1_test_3_retry_3_id)

    api.CITest.discover(suite_1_test_4_parametrized_1_id)
    api.CITest.discover(suite_1_test_4_parametrized_2_id)
    api.CITest.discover(suite_1_test_4_parametrized_3_id)

    module_2_id = api.CIModuleId("module_2")
    suite_2_id = api.CISuiteId(module_2_id, "suite_2")
    suite_2_test_1_id = api.CITestId(suite_2_id, "test_1")
    suite_2_test_2_parametrized_1_id = api.CITestId(suite_2_id, "test_2", parameters=json.dumps({"param1": "value1"}))
    suite_2_test_2_parametrized_2_id = api.CITestId(suite_2_id, "test_2", parameters=json.dumps({"param1": "value2"}))
    suite_2_test_2_parametrized_3_id = api.CITestId(suite_2_id, "test_2", parameters=json.dumps({"param1": "value3"}))
    suite_2_test_2_parametrized_4_id = api.CITestId(suite_2_id, "test_2", parameters=json.dumps({"param1": "value4"}))
    suite_2_test_2_parametrized_5_id = api.CITestId(suite_2_id, "test_2", parameters=json.dumps({"param1": "value5"}))
    suite_2_test_2_source_file_info = api.CISourceFileInfo(Path("test_file_2.py"), 8, 9)
    suite_2_test_3_id = api.CITestId(suite_2_id, "test_3")

    api.CIModule.discover(module_2_id)
    api.CISuite.discover(suite_2_id)
    api.CITest.discover(suite_2_test_1_id, source_file_info=api.CISourceFileInfo(Path("my_file_1.py"), 1, 2))
    api.CITest.discover(suite_2_test_2_parametrized_1_id, source_file_info=suite_2_test_2_source_file_info)
    api.CITest.discover(suite_2_test_2_parametrized_2_id, source_file_info=suite_2_test_2_source_file_info)
    api.CITest.discover(suite_2_test_2_parametrized_3_id, source_file_info=suite_2_test_2_source_file_info)
    api.CITest.discover(suite_2_test_2_parametrized_4_id, source_file_info=suite_2_test_2_source_file_info)
    api.CITest.discover(suite_2_test_2_parametrized_5_id, source_file_info=suite_2_test_2_source_file_info)

    api.CITest.discover(
        suite_2_test_3_id,
        codeowners=["@romain"],
        source_file_info=api.CISourceFileInfo(Path("my_file_1.py"), 4, 12),
        is_early_flake_detection=True,
    )

    module_3_id = api.CIModuleId("module_3")
    suite_3_id = api.CISuiteId(module_3_id, "suite_3")
    suite_4_id = api.CISuiteId(module_3_id, "suite_4")

    suite_3_test_1_id = api.CITestId(suite_3_id, "test_1")
    suite_3_test_2_id = api.CITestId(suite_3_id, "test_2")
    suite_3_test_3_id = api.CITestId(suite_3_id, "test_3")

    suite_4_test_1_id = api.CITestId(suite_4_id, "test_1")
    suite_4_test_2_id = api.CITestId(suite_4_id, "test_2")
    suite_4_test_3_id = api.CITestId(suite_4_id, "test_3")

    api.CIModule.discover(module_3_id)

    api.CISuite.discover(suite_3_id)

    api.CITest.discover(suite_3_test_1_id, source_file_info=api.CISourceFileInfo(Path("module_3/suite_3.py"), 4, 6))
    api.CITest.discover(suite_3_test_2_id, source_file_info=api.CISourceFileInfo(Path("module_3/suite_3.py"), 9, 12))
    api.CITest.discover(suite_3_test_3_id, source_file_info=api.CISourceFileInfo(Path("module_3/suite_3.py"), 16, 48))

    api.CISuite.discover(suite_4_id)
    api.CITest.discover(suite_4_test_1_id, source_file_info=api.CISourceFileInfo(Path("module_3/suite_4.py"), 4, 6))
    api.CITest.discover(suite_4_test_2_id, source_file_info=api.CISourceFileInfo(Path("module_3/suite_4.py"), 9, 12))
    api.CITest.discover(suite_4_test_3_id, source_file_info=api.CISourceFileInfo(Path("module_3/suite_4.py"), 16, 48))

    module_4_id = api.CIModuleId("module_4")
    suite_5_id = api.CISuiteId(module_4_id, "suite_5")
    suite_6_id = api.CISuiteId(module_4_id, "suite_6")

    suite_5_test_1_id = api.CITestId(suite_5_id, "test_1")
    suite_5_test_2_id = api.CITestId(suite_5_id, "test_2")
    suite_5_test_3_id = api.CITestId(suite_5_id, "test_3")

    suite_6_test_1_id = api.CITestId(suite_6_id, "test_1")
    suite_6_test_2_id = api.CITestId(suite_6_id, "test_2")
    suite_6_test_3_id = api.CITestId(suite_6_id, "test_3")

    api.CIModule.discover(module_4_id)

    api.CISuite.discover(suite_5_id)

    api.CITest.discover(suite_5_test_1_id, source_file_info=api.CISourceFileInfo(Path("module_5/suite_5.py"), 4, 6))
    api.CITest.discover(suite_5_test_2_id, source_file_info=api.CISourceFileInfo(Path("module_5/suite_5.py"), 9, 12))
    api.CITest.discover(suite_5_test_3_id, source_file_info=api.CISourceFileInfo(Path("module_5/suite_5.py"), 16, 48))

    api.CISuite.discover(suite_6_id)
    api.CISuite.mark_itr_unskippable(suite_6_id)
    api.CITest.discover(suite_6_test_1_id)
    api.CITest.discover(suite_6_test_2_id)
    api.CITest.discover(suite_6_test_3_id)

    # END DISCOVERY

    # START TESTS

    api.CIModule.start(module_1_id)

    api.CISuite.start(suite_1_id)

    #
    # suite_1_test_1 test
    api.CITest.start(suite_1_test_1_id)
    api.CITest.add_coverage_data(suite_1_test_1_id, {Path("my_file_1.py"): [(1, 3), (4, 8), (9, 9)]})
    api.CITest.mark_pass(suite_1_test_1_id)

    #
    # suite_1_test_2 test
    api.CITest.start(suite_1_test_2_id)
    api.CITest.set_tag(suite_1_test_2_id, "test.tag1", "suite_1_test_2_id")
    api.CITest.add_coverage_data(
        suite_1_id,
        {
            Path("my_file_1.py"): [(1, 3), (4, 8), (9, 9)],
            Path("my/other/path/my_file_2.py"): [(1, 9), (10, 10000000)],
        },
    )
    api.CITest.mark_skip(suite_1_test_2_id)

    #
    # suite_1_test_3 test and EFD retries
    api.CITest.start(suite_1_test_3_id)
    suite_1_test_3_abs_path_1 = Path("my_abs_file_3.py").absolute()
    api.CITest.add_coverage_data(suite_1_id, {suite_1_test_3_abs_path_1: [(1, 3), (4, 8), (9, 9)]})
    suite_1_test_3_rel_path_1 = Path("my_rel_file_3.py")
    api.CITest.add_coverage_data(suite_1_id, {suite_1_test_3_rel_path_1: [(2, 2)]})
    api.CITest.mark_itr_skipped(suite_1_test_3_id)
    #
    api.CITest.start(suite_1_test_3_retry_1_id)
    api.CITest.mark_pass(suite_1_test_3_retry_1_id)
    #
    api.CITest.start(suite_1_test_3_retry_2_id)
    api.CITest.mark_pass(suite_1_test_3_retry_2_id)
    #
    api.CITest.start(suite_1_test_3_retry_3_id)
    api.CITest.add_coverage_data(
        suite_1_id,
        {
            Path("my_file_1.py"): [(1, 3), (4, 8), (9, 9)],
            Path("my/other/path/my_file_2.py"): [(1, 9), (10, 10000000)],
            Path("my_abs_file_3.py").absolute(): [(1, 1)],
            Path("my_rel_file_3.py"): [(1, 1), (3, 6), (79, 97)],
        },
    )
    api.CITest.mark_pass(suite_1_test_3_retry_3_id)

    #
    # suite1_test_4 parametrized tests
    api.CITest.start(suite_1_test_4_parametrized_1_id)
    api.CITest.set_tags(
        suite_1_test_4_parametrized_1_id,
        {
            "test.tag1": "suite_1_test_4_parametrized_1_id",
            "test.tag2": "value_for_tag_2",
            "test.tag3": "this should be deleted",
            "test.tag4": 4,
            "test.tag5": "this should also be deleted",
        },
    )
    api.CITest.delete_tag(suite_1_test_4_parametrized_1_id, "test.tag3")
    api.CITest.delete_tag(suite_1_test_4_parametrized_1_id, "test.tag5")
    api.CITest.mark_skip(suite_1_test_4_parametrized_1_id)
    #
    api.CITest.start(suite_1_test_4_parametrized_2_id)
    api.CITest.mark_pass(suite_1_test_4_parametrized_2_id)
    #
    api.CITest.set_tag(suite_1_test_4_parametrized_3_id, "test.tag1", "suite_1_test_4_parametrized_3_id")
    api.CITest.set_tag(suite_1_test_4_parametrized_3_id, "test.tag2", "this will be deleted")
    api.CITest.set_tag(suite_1_test_4_parametrized_3_id, "test.tag3", 12333333)
    api.CITest.set_tag(suite_1_test_4_parametrized_3_id, "test.tag4", "this will also be deleted")
    api.CITest.delete_tags(suite_1_test_4_parametrized_3_id, ["test.tag2", "test.tag4"])
    api.CITest.start(suite_1_test_4_parametrized_3_id)
    api.CITest.mark_fail(suite_1_test_4_parametrized_3_id, exc_info=_make_excinfo())
    #
    api.CISuite.finish(suite_1_id)

    api.CIModule.finish(module_1_id)

    api.CIModule.start(module_2_id)

    api.CISuite.start(suite_2_id)

    #
    # suite_2_test_1 test
    api.CITest.start(suite_2_test_1_id)
    api.CITest.mark_skip(suite_2_test_1_id)

    #
    # suite_2_test_2 parametrized tests
    api.CITest.set_tags(
        suite_2_test_2_parametrized_1_id,
        {"test.tag1": "suite_2_test_2_parametrized_1_id", "test.tag2": "two", "test.tag3": 3},
    )
    api.CITest.start(suite_2_test_2_parametrized_1_id)
    api.CITest.mark_pass(suite_2_test_2_parametrized_1_id)
    #
    api.CITest.start(suite_2_test_2_parametrized_2_id)
    api.CITest.set_tag(suite_2_test_2_parametrized_2_id, "test.tag1", "suite_2_test_2_parametrized_2_id")
    api.CITest.delete_tag(suite_2_test_2_parametrized_2_id, "test.tag1")
    api.CITest.mark_pass(suite_2_test_2_parametrized_2_id)
    #
    api.CITest.start(suite_2_test_2_parametrized_3_id)

    api.CITest.mark_itr_skipped(suite_2_test_2_parametrized_3_id)
    #
    api.CITest.start(suite_2_test_2_parametrized_4_id)
    api.CITest.mark_pass(suite_2_test_2_parametrized_4_id)
    #
    api.CITest.start(suite_2_test_2_parametrized_5_id)
    api.CITest.mark_pass(suite_2_test_2_parametrized_5_id)

    #
    # suite_2_test_3 test
    api.CITest.start(suite_2_test_3_id)
    api.CITest.set_tags(
        suite_2_test_3_id, {"test.tag1": "suite_2_test_3_id", "test.tag2": 2, "test.tag3": "this tag stays"}
    )
    api.CITest.mark_skip(suite_2_test_3_id)

    # suite_2 coverage
    suite_2_test_2_parametrized_5_abs_path_1 = Path("my_abs_file_suite_2_5_1.py").absolute()
    suite_2_test_2_parametrized_5_abs_path_2 = Path("my_abs_file_2_5_2.py").absolute()
    # The two paths below should merge into a single file
    suite_2_test_2_parametrized_5_rel_path_1 = Path("my_rel_file_2_5.py").absolute()
    suite_2_test_2_parametrized_5_rel_path_2 = Path("my_rel_file_2_5.py")
    api.CITest.add_coverage_data(
        suite_2_id,
        {
            suite_2_test_2_parametrized_5_abs_path_1: [(1, 3), (2, 6), (10, 12)],
            suite_2_test_2_parametrized_5_abs_path_2: [(1, 3), (2, 6), (10, 12)],
            suite_2_test_2_parametrized_5_rel_path_1: [(1, 2)],
            suite_2_test_2_parametrized_5_rel_path_2: [(3, 3)],
        },
    )

    api.CISuite.finish(suite_2_id)

    api.CIModule.finish(module_2_id)

    api.CIModule.start(module_3_id)

    api.CISuite.start(suite_3_id)

    #
    # suite_3_test_1 test
    api.CITest.start(suite_3_test_1_id)
    api.CITest.add_coverage_data(
        suite_3_id,
        {
            Path("my_file_suite_3_1.py"): [(1, 3), (4, 8), (9, 9)],
            Path("my/other/path/my_file_suite_3_2.py"): [(1, 9), (10, 10000000)],
            Path("my_abs_file_suite_3_3.py").absolute(): [(1, 1)],
            Path("my_rel_file_suite_3_3.py"): [(1, 1), (3, 6), (79, 97)],
        },
    )
    api.CITest.mark_pass(suite_3_test_1_id)
    #
    # suite_3_test_2 test
    api.CITest.start(suite_3_test_2_id)
    api.CITest.mark_fail(suite_3_test_2_id)
    #
    # suite_3_test_3 test
    api.CITest.start(suite_3_test_3_id)
    api.CITest.mark_pass(suite_3_test_3_id)

    suite_2_test_2_parametrized_5_abs_path_1 = Path("my_abs_file_suite_3_1.py").absolute()
    suite_2_test_2_parametrized_5_abs_path_2 = Path("my_abs_file_suite_3_2.py").absolute()
    # The two paths below should merge into a single file
    suite_2_test_2_parametrized_5_rel_path_1 = Path("my_rel_file_suite3_5.py").absolute()
    suite_2_test_2_parametrized_5_rel_path_2 = Path("my_rel_file_suite3_5.py")
    api.CITest.add_coverage_data(
        suite_3_id,
        {
            suite_2_test_2_parametrized_5_abs_path_1: [(1, 3), (2, 6), (10, 12)],
            suite_2_test_2_parametrized_5_abs_path_2: [(1, 3), (2, 6), (10, 12)],
            suite_2_test_2_parametrized_5_rel_path_1: [(1, 2)],
            suite_2_test_2_parametrized_5_rel_path_2: [(3, 3)],
        },
    )

    api.CISuite.finish(suite_3_id)

    api.CISuite.start(suite_4_id)

    #
    # suite_4_test_1 test
    api.CITest.start(suite_4_test_1_id)
    api.CITest.mark_pass(suite_4_test_1_id)
    #
    # suite_4_test_2 test
    api.CITest.start(suite_4_test_2_id)
    api.CITest.mark_pass(suite_4_test_2_id)
    #
    # suite_4_test_3 test
    api.CITest.start(suite_4_test_3_id)
    api.CITest.mark_pass(suite_4_test_3_id)

    api.CISuite.finish(suite_4_id)

    api.CIModule.finish(module_3_id)

    api.CIModule.start(module_4_id)

    api.CISuite.start(suite_5_id)

    #
    # suite_5_test_1 test
    api.CITest.start(suite_5_test_1_id)
    api.CITest.mark_itr_skipped(suite_5_test_1_id)
    #
    # suite_5_test_2 test
    api.CITest.start(suite_5_test_2_id)
    api.CITest.mark_itr_skipped(suite_5_test_2_id)
    #
    # suite_5_test_3 test
    api.CITest.start(suite_5_test_3_id)
    api.CITest.mark_itr_skipped(suite_5_test_3_id)

    api.CISuite.mark_itr_skipped(suite_5_id)

    api.CISuite.start(suite_6_id)

    #
    # suite_6_test_1 test
    api.CITest.start(suite_6_test_1_id)
    api.CITest.mark_itr_skipped(suite_6_test_1_id)
    #
    # suite_6_test_2 test
    api.CITest.start(suite_6_test_2_id)
    api.CITest.mark_pass(suite_6_test_2_id)
    #
    # suite_6_test_3 test
    api.CITest.start(suite_6_test_3_id)
    api.CITest.mark_itr_skipped(suite_6_test_3_id)

    api.CITest.mark_itr_forced_run(suite_6_id)
    api.CISuite.finish(suite_6_id)

    api.CIModule.finish(module_4_id)

    api.CISession.finish()

    # FINISH TESTS


if __name__ == "__main__":
    freeze_support()
    # NOTE: this is only safe because these tests are run in a subprocess
    import os

    os.environ["_DD_CIVISIBILITY_ITR_SUITE_MODE"] = "1"
    with mock.patch("ddtrace.internal.ci_visibility.CIVisibility.is_itr_enabled", return_value=True):
        main()
