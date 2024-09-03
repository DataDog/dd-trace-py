"""Fake test runner where all tests pass"""

from multiprocessing import freeze_support
from pathlib import Path

from ddtrace.ext.ci_visibility import api
from ddtrace.internal.ci_visibility.utils import take_over_logger_stream_handler


def main():
    take_over_logger_stream_handler()

    api.enable_ci_visibility()

    # START DISCOVERY

    api.CISession.discover("manual_test_all_pass", "dd_manual_test_fw", "1.0.0")

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

    module_2_id = api.CIModuleId("module_2")
    suite_2_id = api.CISuiteId(module_2_id, "suite_2")
    suite_2_test_1_id = api.CITestId(suite_2_id, "test_1")
    suite_2_test_2_id = api.CITestId(suite_2_id, "test_2")
    suite_2_test_3_id = api.CITestId(suite_2_id, "test_3")

    suite_2_test_3_retry_1_id = api.CITestId(suite_2_id, "test_3", retry_number=1)
    suite_2_test_3_retry_2_id = api.CITestId(suite_2_id, "test_3", retry_number=2)
    suite_2_test_3_retry_3_id = api.CITestId(suite_2_id, "test_3", retry_number=3)

    api.CIModule.discover(module_2_id)
    api.CISuite.discover(suite_2_id)
    api.CITest.discover(suite_2_test_1_id, source_file_info=api.CISourceFileInfo(Path("my_file_1.py"), 1, 2))
    api.CITest.discover(suite_2_test_2_id, source_file_info=None)
    api.CITest.discover(
        suite_2_test_3_id,
        codeowners=["@romain"],
        source_file_info=api.CISourceFileInfo(Path("my_file_1.py"), 4, 12),
        is_early_flake_detection=True,
    )
    api.CITest.discover_early_flake_retry(suite_2_test_3_retry_1_id)
    api.CITest.discover_early_flake_retry(suite_2_test_3_retry_2_id)
    api.CITest.discover_early_flake_retry(suite_2_test_3_retry_3_id)

    # END DISCOVERY

    api.CISession.start()

    api.CIModule.start(module_1_id)

    api.CISuite.start(suite_1_id)

    api.CITest.start(suite_1_test_1_id)
    api.CITest.finish(suite_1_test_1_id, api.CITestStatus.PASS)
    api.CITest.start(suite_1_test_2_id)
    api.CITest.finish(suite_1_test_2_id, api.CITestStatus.PASS)
    api.CITest.start(suite_1_test_3_id)
    api.CITest.finish(suite_1_test_3_id, api.CITestStatus.PASS)
    api.CITest.start(suite_1_test_3_retry_1_id)
    api.CITest.finish(suite_1_test_3_retry_1_id, api.CITestStatus.PASS)
    api.CITest.start(suite_1_test_3_retry_2_id)
    api.CITest.finish(suite_1_test_3_retry_2_id, api.CITestStatus.PASS)
    api.CITest.start(suite_1_test_3_retry_3_id)
    api.CITest.finish(suite_1_test_3_retry_3_id, api.CITestStatus.PASS)

    api.CISuite.finish(suite_1_id)

    api.CIModule.finish(module_1_id)

    api.CIModule.start(module_2_id)

    api.CISuite.start(suite_2_id)

    api.CITest.start(suite_2_test_1_id)
    api.CITest.mark_pass(suite_2_test_1_id)
    api.CITest.start(suite_2_test_2_id)
    api.CITest.mark_pass(suite_2_test_2_id)
    api.CITest.start(suite_2_test_3_id)
    api.CITest.mark_pass(suite_2_test_3_id)
    api.CITest.start(suite_2_test_3_retry_1_id)
    api.CITest.mark_pass(suite_2_test_3_retry_1_id)
    api.CITest.start(suite_2_test_3_retry_2_id)
    api.CITest.mark_pass(suite_2_test_3_retry_2_id)
    api.CITest.start(suite_2_test_3_retry_3_id)
    api.CITest.mark_pass(suite_2_test_3_retry_3_id)

    api.CISuite.finish(suite_2_id)

    api.CIModule.finish(module_2_id)

    api.CISession.finish()


if __name__ == "__main__":
    freeze_support()
    main()
