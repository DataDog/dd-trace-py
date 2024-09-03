"""Fake test runner where tests have a mix of fail, skip or pass, and the final session fails

Incorporates setting and deleting tags, as well.
Starts session before discovery (simulating pytest behavior)

Module 1 should fail, suite 1 should fail
Module 2 should pass, suite 2 should pass
Module 3 should fail, suite 3 should fail but suite 4 should pass
Module 4 should pass, but be marked forced run, suite 6 should be marked as forced run,
and suite 6 test 2 should be marked as forced run and unskippable

Entire session should be marked as forced run

ITR coverage data is added at the test level.

Comment lines in the test start/finish lines are there for visual distinction.
"""
import json
from multiprocessing import freeze_support
from pathlib import Path
import sys
from unittest import mock

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.internal.test_visibility import api
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


def _make_excinfo():
    try:
        raise ValueError("This is a fake exception")
    except ValueError:
        return ext_api.TestExcInfo(*sys.exc_info())


def main():
    ext_api.enable_test_visibility()

    # START DISCOVERY

    api.InternalTestSession.discover("manual_test_mix_fail_itr_test_level", "dd_manual_test_fw", "1.0.0")
    api.InternalTestSession.start()

    module_1_id = ext_api.TestModuleId("module_1")

    api.InternalTestModule.discover(module_1_id)

    suite_1_id = ext_api.TestSuiteId(module_1_id, "suite_1")
    api.InternalTestSuite.discover(suite_1_id)

    suite_1_test_1_id = api.InternalTestId(suite_1_id, "test_1")
    suite_1_test_2_id = api.InternalTestId(suite_1_id, "test_2")
    suite_1_test_3_id = api.InternalTestId(suite_1_id, "test_3")
    suite_1_test_3_retry_1_id = api.InternalTestId(suite_1_id, "test_3", retry_number=1)
    suite_1_test_3_retry_2_id = api.InternalTestId(suite_1_id, "test_3", retry_number=2)
    suite_1_test_3_retry_3_id = api.InternalTestId(suite_1_id, "test_3", retry_number=3)

    suite_1_test_4_parametrized_1_id = api.InternalTestId(
        suite_1_id, "test_4", parameters=json.dumps({"param1": "value1"})
    )
    suite_1_test_4_parametrized_2_id = api.InternalTestId(
        suite_1_id, "test_4", parameters=json.dumps({"param1": "value2"})
    )
    suite_1_test_4_parametrized_3_id = api.InternalTestId(
        suite_1_id, "test_4", parameters=json.dumps({"param1": "value3"})
    )

    api.InternalTest.discover(
        suite_1_test_1_id, source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 1, 2)
    )
    api.InternalTest.discover(suite_1_test_2_id, source_file_info=None)
    api.InternalTest.discover(
        suite_1_test_3_id,
        codeowners=["@romain", "@romain2"],
        source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 4, 12),
        is_early_flake_detection=True,
    )
    api.InternalTest.discover_early_flake_retry(suite_1_test_3_retry_1_id)
    api.InternalTest.discover_early_flake_retry(suite_1_test_3_retry_2_id)
    api.InternalTest.discover_early_flake_retry(suite_1_test_3_retry_3_id)

    api.InternalTest.discover(suite_1_test_4_parametrized_1_id)
    api.InternalTest.discover(suite_1_test_4_parametrized_2_id)
    api.InternalTest.discover(suite_1_test_4_parametrized_3_id)

    module_2_id = ext_api.TestModuleId("module_2")
    suite_2_id = ext_api.TestSuiteId(module_2_id, "suite_2")
    suite_2_test_1_id = api.InternalTestId(suite_2_id, "test_1")
    suite_2_test_2_parametrized_1_id = api.InternalTestId(
        suite_2_id, "test_2", parameters=json.dumps({"param1": "value1"})
    )
    suite_2_test_2_parametrized_2_id = api.InternalTestId(
        suite_2_id, "test_2", parameters=json.dumps({"param1": "value2"})
    )
    suite_2_test_2_parametrized_3_id = api.InternalTestId(
        suite_2_id, "test_2", parameters=json.dumps({"param1": "value3"})
    )
    suite_2_test_2_parametrized_4_id = api.InternalTestId(
        suite_2_id, "test_2", parameters=json.dumps({"param1": "value4"})
    )
    suite_2_test_2_parametrized_5_id = api.InternalTestId(
        suite_2_id, "test_2", parameters=json.dumps({"param1": "value5"})
    )
    suite_2_test_2_source_file_info = ext_api.TestSourceFileInfo(Path("test_file_2.py"), 8, 9)
    suite_2_test_3_id = api.InternalTestId(suite_2_id, "test_3")

    api.InternalTestModule.discover(module_2_id)
    api.InternalTestSuite.discover(suite_2_id)
    api.InternalTest.discover(
        suite_2_test_1_id, source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 1, 2)
    )
    api.InternalTest.discover(suite_2_test_2_parametrized_1_id, source_file_info=suite_2_test_2_source_file_info)
    api.InternalTest.discover(suite_2_test_2_parametrized_2_id, source_file_info=suite_2_test_2_source_file_info)
    api.InternalTest.discover(suite_2_test_2_parametrized_3_id, source_file_info=suite_2_test_2_source_file_info)
    api.InternalTest.discover(suite_2_test_2_parametrized_4_id, source_file_info=suite_2_test_2_source_file_info)
    api.InternalTest.discover(suite_2_test_2_parametrized_5_id, source_file_info=suite_2_test_2_source_file_info)

    api.InternalTest.discover(
        suite_2_test_3_id,
        codeowners=["@romain"],
        source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 4, 12),
        is_early_flake_detection=True,
    )

    module_3_id = ext_api.TestModuleId("module_3")
    suite_3_id = ext_api.TestSuiteId(module_3_id, "suite_3")
    suite_4_id = ext_api.TestSuiteId(module_3_id, "suite_4")

    suite_3_test_1_id = api.InternalTestId(suite_3_id, "test_1")
    suite_3_test_2_id = api.InternalTestId(suite_3_id, "test_2")
    suite_3_test_3_id = api.InternalTestId(suite_3_id, "test_3")

    suite_4_test_1_id = api.InternalTestId(suite_4_id, "test_1")
    suite_4_test_2_id = api.InternalTestId(suite_4_id, "test_2")
    suite_4_test_3_id = api.InternalTestId(suite_4_id, "test_3")

    api.InternalTestModule.discover(module_3_id)

    api.InternalTestSuite.discover(suite_3_id)

    api.InternalTest.discover(
        suite_3_test_1_id, source_file_info=ext_api.TestSourceFileInfo(Path("module_3/suite_3.py"), 4, 6)
    )
    api.InternalTest.discover(
        suite_3_test_2_id, source_file_info=ext_api.TestSourceFileInfo(Path("module_3/suite_3.py"), 9, 12)
    )
    api.InternalTest.discover(
        suite_3_test_3_id, source_file_info=ext_api.TestSourceFileInfo(Path("module_3/suite_3.py"), 16, 48)
    )

    api.InternalTestSuite.discover(suite_4_id)
    api.InternalTest.discover(
        suite_4_test_1_id, source_file_info=ext_api.TestSourceFileInfo(Path("module_3/suite_4.py"), 4, 6)
    )
    api.InternalTest.discover(
        suite_4_test_2_id, source_file_info=ext_api.TestSourceFileInfo(Path("module_3/suite_4.py"), 9, 12)
    )
    api.InternalTest.discover(
        suite_4_test_3_id, source_file_info=ext_api.TestSourceFileInfo(Path("module_3/suite_4.py"), 16, 48)
    )

    module_4_id = ext_api.TestModuleId("module_4")
    suite_5_id = ext_api.TestSuiteId(module_4_id, "suite_5")
    suite_6_id = ext_api.TestSuiteId(module_4_id, "suite_6")

    suite_5_test_1_id = api.InternalTestId(suite_5_id, "test_1")
    suite_5_test_2_id = api.InternalTestId(suite_5_id, "test_2")
    suite_5_test_3_id = api.InternalTestId(suite_5_id, "test_3")

    suite_6_test_1_id = api.InternalTestId(suite_6_id, "test_1")
    suite_6_test_2_id = api.InternalTestId(suite_6_id, "test_2")
    suite_6_test_3_id = api.InternalTestId(suite_6_id, "test_3")

    api.InternalTestModule.discover(module_4_id)

    api.InternalTestSuite.discover(suite_5_id)

    api.InternalTest.discover(
        suite_5_test_1_id, source_file_info=ext_api.TestSourceFileInfo(Path("module_5/suite_5.py"), 4, 6)
    )
    api.InternalTest.discover(
        suite_5_test_2_id, source_file_info=ext_api.TestSourceFileInfo(Path("module_5/suite_5.py"), 9, 12)
    )
    api.InternalTest.discover(
        suite_5_test_3_id, source_file_info=ext_api.TestSourceFileInfo(Path("module_5/suite_5.py"), 16, 48)
    )

    api.InternalTestSuite.discover(suite_6_id)
    api.InternalTest.discover(suite_6_test_1_id)
    api.InternalTest.discover(suite_6_test_2_id)
    api.InternalTest.mark_itr_unskippable(suite_6_test_2_id)
    api.InternalTest.discover(suite_6_test_3_id)

    # END DISCOVERY

    # START TESTS

    api.InternalTestModule.start(module_1_id)

    api.InternalTestSuite.start(suite_1_id)

    #
    # suite_1_test_1 test
    api.InternalTest.start(suite_1_test_1_id)
    api.InternalTest.add_coverage_data(
        suite_1_test_1_id, {Path("my_file_1.py"): CoverageLines.from_list([1, 2, 3, 4, 5, 6, 7, 8, 9])}
    )
    api.InternalTest.mark_pass(suite_1_test_1_id)

    #
    # suite_1_test_2 test
    api.InternalTest.start(suite_1_test_2_id)
    api.InternalTest.set_tag(suite_1_test_2_id, "test.tag1", "suite_1_test_2_id")
    api.InternalTest.add_coverage_data(
        suite_1_test_2_id,
        {
            Path("my_file_1.py"): CoverageLines.from_list([1, 2, 3, 4, 5, 6, 7, 8, 9]),
            Path("my/other/path/my_file_2.py"): CoverageLines.from_list(list(range(1, 10)) + list(range(10, 101))),
        },
    )
    api.InternalTest.mark_skip(suite_1_test_2_id)

    #
    # suite_1_test_3 test and EFD retries
    api.InternalTest.start(suite_1_test_3_id)
    suite_1_test_3_abs_path_1 = Path("my_abs_file_3.py").absolute()
    api.InternalTest.add_coverage_data(
        suite_1_test_3_id, {suite_1_test_3_abs_path_1: CoverageLines.from_list([1, 2, 3, 4, 8, 9])}
    )
    suite_1_test_3_rel_path_1 = Path("my_rel_file_3.py")
    api.InternalTest.add_coverage_data(suite_1_test_3_id, {suite_1_test_3_rel_path_1: CoverageLines.from_list([2])})
    api.InternalTest.mark_itr_skipped(suite_1_test_3_id)
    #
    api.InternalTest.start(suite_1_test_3_retry_1_id)
    api.InternalTest.mark_pass(suite_1_test_3_retry_1_id)
    #
    api.InternalTest.start(suite_1_test_3_retry_2_id)
    api.InternalTest.mark_pass(suite_1_test_3_retry_2_id)
    #
    api.InternalTest.start(suite_1_test_3_retry_3_id)
    api.InternalTest.add_coverage_data(
        suite_1_test_2_id,
        {
            Path("my_file_1.py"): CoverageLines.from_list([1, 2, 3, 4, 5, 6, 7, 8, 9]),
            Path("my/other/path/my_file_2.py"): CoverageLines.from_list(list(range(1, 10)) + list(range(10, 101))),
            Path("my_abs_file_3.py").absolute(): CoverageLines.from_list([1]),
            Path("my_rel_file_3.py"): CoverageLines.from_list([1, 3, 4, 5, 6] + list(range(79, 98))),
        },
    )
    api.InternalTest.mark_pass(suite_1_test_3_retry_3_id)

    #
    # suite1_test_4 parametrized tests
    api.InternalTest.start(suite_1_test_4_parametrized_1_id)
    api.InternalTest.set_tags(
        suite_1_test_4_parametrized_1_id,
        {
            "test.tag1": "suite_1_test_4_parametrized_1_id",
            "test.tag2": "value_for_tag_2",
            "test.tag3": "this should be deleted",
            "test.tag4": 4,
            "test.tag5": "this should also be deleted",
        },
    )
    api.InternalTest.delete_tag(suite_1_test_4_parametrized_1_id, "test.tag3")
    api.InternalTest.delete_tag(suite_1_test_4_parametrized_1_id, "test.tag5")
    api.InternalTest.mark_skip(suite_1_test_4_parametrized_1_id)
    #
    api.InternalTest.start(suite_1_test_4_parametrized_2_id)
    api.InternalTest.mark_pass(suite_1_test_4_parametrized_2_id)
    #
    api.InternalTest.set_tag(suite_1_test_4_parametrized_3_id, "test.tag1", "suite_1_test_4_parametrized_3_id")
    api.InternalTest.set_tag(suite_1_test_4_parametrized_3_id, "test.tag2", "this will be deleted")
    api.InternalTest.set_tag(suite_1_test_4_parametrized_3_id, "test.tag3", 12333333)
    api.InternalTest.set_tag(suite_1_test_4_parametrized_3_id, "test.tag4", "this will also be deleted")
    api.InternalTest.delete_tags(suite_1_test_4_parametrized_3_id, ["test.tag2", "test.tag4"])
    api.InternalTest.start(suite_1_test_4_parametrized_3_id)
    api.InternalTest.mark_fail(suite_1_test_4_parametrized_3_id, exc_info=_make_excinfo())
    #
    api.InternalTestSuite.finish(suite_1_id)

    api.InternalTestModule.finish(module_1_id)

    api.InternalTestModule.start(module_2_id)

    api.InternalTestSuite.start(suite_2_id)

    #
    # suite_2_test_1 test
    api.InternalTest.start(suite_2_test_1_id)
    api.InternalTest.mark_skip(suite_2_test_1_id)

    #
    # suite_2_test_2 parametrized tests
    api.InternalTest.set_tags(
        suite_2_test_2_parametrized_1_id,
        {"test.tag1": "suite_2_test_2_parametrized_1_id", "test.tag2": "two", "test.tag3": 3},
    )
    api.InternalTest.start(suite_2_test_2_parametrized_1_id)
    api.InternalTest.mark_pass(suite_2_test_2_parametrized_1_id)
    #
    api.InternalTest.start(suite_2_test_2_parametrized_2_id)
    api.InternalTest.set_tag(suite_2_test_2_parametrized_2_id, "test.tag1", "suite_2_test_2_parametrized_2_id")
    api.InternalTest.delete_tag(suite_2_test_2_parametrized_2_id, "test.tag1")
    api.InternalTest.mark_pass(suite_2_test_2_parametrized_2_id)
    #
    api.InternalTest.start(suite_2_test_2_parametrized_3_id)

    api.InternalTest.mark_itr_skipped(suite_2_test_2_parametrized_3_id)
    #
    api.InternalTest.start(suite_2_test_2_parametrized_4_id)
    api.InternalTest.mark_pass(suite_2_test_2_parametrized_4_id)
    #
    api.InternalTest.start(suite_2_test_2_parametrized_5_id)
    suite_2_test_2_parametrized_5_abs_path_1 = Path("my_abs_file_5_1.py").absolute()
    suite_2_test_2_parametrized_5_abs_path_2 = Path("my_abs_file_5_2.py").absolute()
    # The two paths below should merge into a single file
    suite_2_test_2_parametrized_5_rel_path_1 = Path("my_rel_file_5.py").absolute()
    suite_2_test_2_parametrized_5_rel_path_2 = Path("my_rel_file_5.py")
    api.InternalTest.add_coverage_data(
        suite_2_test_2_parametrized_5_id,
        {
            suite_2_test_2_parametrized_5_abs_path_1: CoverageLines.from_list([1, 2, 3, 4, 5, 6, 10, 11, 12]),
            suite_2_test_2_parametrized_5_abs_path_2: CoverageLines.from_list([1, 2, 3, 4, 5, 6, 10, 11, 12]),
            suite_2_test_2_parametrized_5_rel_path_1: CoverageLines.from_list([1, 2]),
            suite_2_test_2_parametrized_5_rel_path_2: CoverageLines.from_list([3]),
        },
    )
    api.InternalTest.mark_pass(suite_2_test_2_parametrized_5_id)

    #
    # suite_2_test_3 test
    api.InternalTest.start(suite_2_test_3_id)
    api.InternalTest.set_tags(
        suite_2_test_3_id, {"test.tag1": "suite_2_test_3_id", "test.tag2": 2, "test.tag3": "this tag stays"}
    )
    api.InternalTest.mark_skip(suite_2_test_3_id)

    api.InternalTestSuite.finish(suite_2_id)

    api.InternalTestModule.finish(module_2_id)

    api.InternalTestModule.start(module_3_id)

    api.InternalTestSuite.start(suite_3_id)

    #
    # suite_3_test_1 test
    api.InternalTest.start(suite_3_test_1_id)
    api.InternalTest.mark_pass(suite_3_test_1_id)
    #
    # suite_3_test_2 test
    api.InternalTest.start(suite_3_test_2_id)
    api.InternalTest.mark_fail(suite_3_test_2_id)
    #
    # suite_3_test_3 test
    api.InternalTest.start(suite_3_test_3_id)
    api.InternalTest.mark_pass(suite_3_test_3_id)

    api.InternalTestSuite.finish(suite_3_id)

    api.InternalTestSuite.start(suite_4_id)

    #
    # suite_4_test_1 test
    api.InternalTest.start(suite_4_test_1_id)
    api.InternalTest.mark_pass(suite_4_test_1_id)
    #
    # suite_4_test_2 test
    api.InternalTest.start(suite_4_test_2_id)
    api.InternalTest.mark_pass(suite_4_test_2_id)
    #
    # suite_4_test_3 test
    api.InternalTest.start(suite_4_test_3_id)
    api.InternalTest.mark_pass(suite_4_test_3_id)

    api.InternalTestSuite.finish(suite_4_id)

    api.InternalTestModule.finish(module_3_id)

    api.InternalTestModule.start(module_4_id)

    api.InternalTestSuite.start(suite_5_id)

    #
    # suite_5_test_1 test
    api.InternalTest.start(suite_5_test_1_id)
    api.InternalTest.mark_itr_skipped(suite_5_test_1_id)
    #
    # suite_5_test_2 test
    api.InternalTest.start(suite_5_test_2_id)
    api.InternalTest.mark_itr_skipped(suite_5_test_2_id)
    #
    # suite_5_test_3 test
    api.InternalTest.start(suite_5_test_3_id)
    api.InternalTest.mark_itr_skipped(suite_5_test_3_id)

    api.InternalTestSuite.mark_itr_skipped(suite_5_id)

    api.InternalTestSuite.start(suite_6_id)

    #
    # suite_6_test_1 test
    api.InternalTest.start(suite_6_test_1_id)
    api.InternalTest.mark_itr_skipped(suite_6_test_1_id)
    #
    # suite_6_test_2 test
    api.InternalTest.start(suite_6_test_2_id)
    api.InternalTest.mark_itr_forced_run(suite_6_test_2_id)
    api.InternalTest.mark_pass(suite_6_test_2_id)
    #
    # suite_6_test_3 test
    api.InternalTest.start(suite_6_test_3_id)
    api.InternalTest.mark_itr_skipped(suite_6_test_3_id)

    api.InternalTestSuite.finish(suite_6_id)

    api.InternalTestModule.finish(module_4_id)

    api.InternalTestSession.finish()

    # FINISH TESTS


if __name__ == "__main__":
    freeze_support()
    with mock.patch("ddtrace.internal.ci_visibility.CIVisibility.is_itr_enabled", return_value=True):
        main()
