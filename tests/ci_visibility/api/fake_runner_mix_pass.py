"""Fake test runner where all tests either skip or pass, and the final session passes

Incorporates setting and deleting tags, as well.
Starts session before discovery (simulating pytest behavior)

Comment lines in the test start/finish lines are there for visual distinction.
"""
import json
from multiprocessing import freeze_support
from pathlib import Path

from ddtrace.ext.test_visibility import api


def main():
    api.enable_test_visibility()

    # START DISCOVERY

    api.TestSession.discover("manual_test_mix_pass", "dd_manual_test_fw", "1.0.0")
    api.TestSession.start()

    module_1_id = api.TestModuleId("module_1")

    api.TestModule.discover(module_1_id)

    suite_1_id = api.TestSuiteId(module_1_id, "suite_1")
    api.TestSuite.discover(suite_1_id)

    suite_1_test_1_id = api.TestId(suite_1_id, "test_1")
    suite_1_test_2_id = api.TestId(suite_1_id, "test_2")
    suite_1_test_3_id = api.TestId(suite_1_id, "test_3")

    suite_1_test_4_parametrized_1_id = api.TestId(suite_1_id, "test_4", parameters=json.dumps({"param1": "value1"}))
    suite_1_test_4_parametrized_2_id = api.TestId(suite_1_id, "test_4", parameters=json.dumps({"param1": "value2"}))
    suite_1_test_4_parametrized_3_id = api.TestId(suite_1_id, "test_4", parameters=json.dumps({"param1": "value3"}))

    api.Test.discover(suite_1_test_1_id, source_file_info=api.TestSourceFileInfo(Path("my_file_1.py"), 1, 2))
    api.Test.discover(suite_1_test_2_id, source_file_info=None)
    api.Test.discover(
        suite_1_test_3_id,
        codeowners=["@romain", "@romain2"],
        source_file_info=api.TestSourceFileInfo(Path("my_file_1.py"), 4, 12),
        is_early_flake_detection=True,
    )

    api.Test.discover(suite_1_test_4_parametrized_1_id)
    api.Test.discover(suite_1_test_4_parametrized_2_id)
    api.Test.discover(suite_1_test_4_parametrized_3_id)

    module_2_id = api.TestModuleId("module_2")
    suite_2_id = api.TestSuiteId(module_2_id, "suite_2")
    suite_2_test_1_id = api.TestId(suite_2_id, "test_1")

    # This test sets parameters after the test ID is created, but since the parameters are not used in the ID, the test
    # names need to be unique
    suite_2_test_2_parametrized_1_id = api.TestId(suite_2_id, "test_2_1")
    suite_2_test_2_parametrized_2_id = api.TestId(suite_2_id, "test_2_2")
    suite_2_test_2_parametrized_3_id = api.TestId(suite_2_id, "test_2_3")
    suite_2_test_2_parametrized_4_id = api.TestId(suite_2_id, "test_2_4")
    suite_2_test_2_parametrized_5_id = api.TestId(suite_2_id, "test_2_5")
    suite_2_test_2_source_file_info = api.TestSourceFileInfo(Path("my_file_2.py"), 8, 9)
    suite_2_test_3_id = api.TestId(suite_2_id, "test_3")

    api.TestModule.discover(module_2_id)
    api.TestSuite.discover(suite_2_id)
    api.Test.discover(suite_2_test_1_id, source_file_info=api.TestSourceFileInfo(Path("my_file_1.py"), 1, 2))
    api.Test.discover(suite_2_test_2_parametrized_1_id, source_file_info=suite_2_test_2_source_file_info)
    api.Test.set_parameters(suite_2_test_2_parametrized_1_id, json.dumps({"param1": "value1"}))
    api.Test.discover(suite_2_test_2_parametrized_2_id, source_file_info=suite_2_test_2_source_file_info)
    api.Test.set_parameters(suite_2_test_2_parametrized_2_id, json.dumps({"param1": "value2"}))
    api.Test.discover(suite_2_test_2_parametrized_3_id, source_file_info=suite_2_test_2_source_file_info)
    api.Test.set_parameters(suite_2_test_2_parametrized_3_id, json.dumps({"param1": "value3"}))
    api.Test.discover(suite_2_test_2_parametrized_4_id, source_file_info=suite_2_test_2_source_file_info)
    api.Test.set_parameters(suite_2_test_2_parametrized_4_id, json.dumps({"param1": "value4"}))
    api.Test.discover(suite_2_test_2_parametrized_5_id, source_file_info=suite_2_test_2_source_file_info)
    api.Test.set_parameters(suite_2_test_2_parametrized_5_id, json.dumps({"param1": "value5"}))

    api.Test.discover(
        suite_2_test_3_id,
        codeowners=["@romain"],
        source_file_info=api.TestSourceFileInfo(Path("my_file_1.py"), 4, 12),
        is_early_flake_detection=True,
    )

    # END DISCOVERY

    # START TESTS

    api.TestModule.start(module_1_id)

    api.TestSuite.start(suite_1_id)

    #
    # suite_1_test_1 test
    api.Test.start(suite_1_test_1_id)
    api.Test.mark_pass(suite_1_test_1_id)

    #
    # suite_1_test_2 test
    api.Test.start(suite_1_test_2_id)
    api.Test.set_tag(suite_1_test_2_id, "test.tag1", "suite_1_test_2_id")
    api.Test.mark_skip(suite_1_test_2_id)

    #
    # suite_1_test_3 test and EFD retries
    api.Test.start(suite_1_test_3_id)
    api.Test.mark_skip(suite_1_test_3_id)
    #

    #
    # suite1_test_4 parametrized tests
    api.Test.start(suite_1_test_4_parametrized_1_id)
    api.Test.set_tags(
        suite_1_test_4_parametrized_1_id,
        {
            "test.tag1": "suite_1_test_4_parametrized_1_id",
            "test.tag2": "value_for_tag_2",
            "test.tag3": "this should be deleted",
            "test.tag4": 4,
            "test.tag5": "this should also be deleted",
        },
    )
    api.Test.delete_tag(suite_1_test_4_parametrized_1_id, "test.tag3")
    api.Test.delete_tag(suite_1_test_4_parametrized_1_id, "test.tag5")
    api.Test.mark_skip(suite_1_test_4_parametrized_1_id)
    #
    api.Test.start(suite_1_test_4_parametrized_2_id)
    api.Test.mark_pass(suite_1_test_4_parametrized_2_id)
    #
    api.Test.set_tag(suite_1_test_4_parametrized_3_id, "test.tag1", "suite_1_test_4_parametrized_3_id")
    api.Test.set_tag(suite_1_test_4_parametrized_3_id, "test.tag2", "this will be deleted")
    api.Test.set_tag(suite_1_test_4_parametrized_3_id, "test.tag3", 12333333)
    api.Test.set_tag(suite_1_test_4_parametrized_3_id, "test.tag4", "this will also be deleted")
    api.Test.delete_tags(suite_1_test_4_parametrized_3_id, ["test.tag2", "test.tag4"])
    api.Test.start(suite_1_test_4_parametrized_3_id)
    api.Test.mark_pass(suite_1_test_4_parametrized_3_id)
    #
    api.TestSuite.finish(suite_1_id)

    api.TestModule.finish(module_1_id)

    api.TestModule.start(module_2_id)

    api.TestSuite.start(suite_2_id)

    #
    # suite_2_test_1 test
    api.Test.start(suite_2_test_1_id)
    api.Test.mark_skip(suite_2_test_1_id)

    #
    # suite_2_test_2 parametrized tests
    api.Test.set_tags(
        suite_2_test_2_parametrized_1_id,
        {"test.tag1": "suite_2_test_2_parametrized_1_id", "test.tag2": "two", "test.tag3": 3},
    )
    api.Test.start(suite_2_test_2_parametrized_1_id)
    api.Test.mark_pass(suite_2_test_2_parametrized_1_id)
    #
    api.Test.start(suite_2_test_2_parametrized_2_id)
    api.Test.set_tag(suite_2_test_2_parametrized_2_id, "test.tag1", "suite_2_test_2_parametrized_2_id")
    api.Test.delete_tag(suite_2_test_2_parametrized_2_id, "test.tag1")
    api.Test.mark_pass(suite_2_test_2_parametrized_2_id)
    #
    api.Test.start(suite_2_test_2_parametrized_3_id)
    api.Test.mark_skip(suite_2_test_2_parametrized_3_id)
    #
    api.Test.start(suite_2_test_2_parametrized_4_id)
    api.Test.mark_pass(suite_2_test_2_parametrized_4_id)
    #
    api.Test.start(suite_2_test_2_parametrized_5_id)
    api.Test.mark_pass(suite_2_test_2_parametrized_5_id)

    #
    # suite_2_test_3 test
    api.Test.start(suite_2_test_3_id)
    api.Test.set_tags(
        suite_2_test_3_id, {"test.tag1": "suite_2_test_3_id", "test.tag2": 2, "test.tag3": "this tag stays"}
    )
    api.Test.mark_skip(suite_2_test_3_id)

    api.TestSuite.finish(suite_2_id)

    api.TestModule.finish(module_2_id)

    api.TestSession.finish()

    # FINISH TESTS


if __name__ == "__main__":
    freeze_support()
    main()
