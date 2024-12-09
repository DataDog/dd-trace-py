"""Fake test runner where all tests are skipped by ITR at test level"""

from multiprocessing import freeze_support
from pathlib import Path
from unittest import mock

from ddtrace.ext.test_visibility import api as ext_api
from ddtrace.internal.test_visibility import api
import ddtrace.internal.test_visibility._internal_item_ids


def main():
    ext_api.enable_test_visibility()

    # START DISCOVERY

    api.InternalTestSession.discover("manual_test_all_itr_skip", "dd_manual_test_fw", "1.0.0")

    module_1_id = ext_api.TestModuleId("module_1")

    api.InternalTestModule.discover(module_1_id)

    suite_1_id = ext_api.TestSuiteId(module_1_id, "suite_1")
    api.InternalTestSuite.discover(suite_1_id)

    suite_1_test_1_id = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(suite_1_id, "test_1")
    suite_1_test_2_id = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(suite_1_id, "test_2")
    suite_1_test_3_id = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(suite_1_id, "test_3")

    api.InternalTest.discover(
        suite_1_test_1_id, source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 1, 2)
    )
    api.InternalTest.discover(suite_1_test_2_id, source_file_info=None)
    api.InternalTest.discover(
        suite_1_test_3_id,
        codeowners=["@romain", "@romain2"],
        source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 4, 12),
    )

    module_2_id = ext_api.TestModuleId("module_2")
    suite_2_id = ext_api.TestSuiteId(module_2_id, "suite_2")
    suite_2_test_1_id = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(suite_2_id, "test_1")
    suite_2_test_2_id = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(suite_2_id, "test_2")
    suite_2_test_3_id = ddtrace.internal.test_visibility._internal_item_ids.InternalTestId(suite_2_id, "test_3")

    api.InternalTestModule.discover(module_2_id)
    api.InternalTestSuite.discover(suite_2_id)
    api.InternalTest.discover(
        suite_2_test_1_id, source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 1, 2)
    )
    api.InternalTest.discover(suite_2_test_2_id, source_file_info=None)
    api.InternalTest.discover(
        suite_2_test_3_id,
        codeowners=["@romain"],
        source_file_info=ext_api.TestSourceFileInfo(Path("my_file_1.py"), 4, 12),
    )

    # END DISCOVERY

    api.InternalTestSession.start()

    api.InternalTestModule.start(module_1_id)

    api.InternalTestSuite.start(suite_1_id)

    api.InternalTest.start(suite_1_test_1_id)
    api.InternalTest.mark_itr_skipped(suite_1_test_1_id)
    api.InternalTest.start(suite_1_test_2_id)
    api.InternalTest.mark_itr_skipped(suite_1_test_2_id)
    api.InternalTest.start(suite_1_test_3_id)
    api.InternalTest.mark_itr_skipped(suite_1_test_3_id)

    api.InternalTestSuite.finish(suite_1_id)

    api.InternalTestModule.finish(module_1_id)

    api.InternalTestModule.start(module_2_id)

    api.InternalTestSuite.start(suite_2_id)

    api.InternalTest.start(suite_2_test_1_id)
    api.InternalTest.mark_itr_skipped(suite_2_test_1_id)
    api.InternalTest.start(suite_2_test_2_id)
    api.InternalTest.mark_itr_skipped(suite_2_test_2_id)
    api.InternalTest.start(suite_2_test_3_id)
    api.InternalTest.mark_itr_skipped(suite_2_test_3_id)

    api.InternalTestSuite.finish(suite_2_id)

    api.InternalTestModule.finish(module_2_id)

    api.InternalTestSession.finish()


if __name__ == "__main__":
    freeze_support()
    with mock.patch("ddtrace.internal.ci_visibility.CIVisibility.is_itr_enabled", return_value=True):
        main()
