"""Fake test runner where all tests fail"""

from multiprocessing import freeze_support
from pathlib import Path
import sys

from ddtrace.ext.test_visibility import api


def _make_excinfo():
    try:
        raise ValueError("This is a fake exception")
    except ValueError:
        return api.TestExcInfo(*sys.exc_info())


def main():
    api.enable_test_visibility()

    # START DISCOVERY

    api.TestSession.discover("manual_test_all_fail", "dd_manual_test_fw", "1.0.0")

    module_1_id = api.TestModuleId("module_1")

    api.TestModule.discover(module_1_id)

    suite_1_id = api.TestSuiteId(module_1_id, "suite_1")
    api.TestSuite.discover(suite_1_id)

    suite_1_test_1_id = api.TestId(suite_1_id, "test_1")
    suite_1_test_2_id = api.TestId(suite_1_id, "test_2")
    suite_1_test_3_id = api.TestId(suite_1_id, "test_3")

    api.Test.discover(suite_1_test_1_id, source_file_info=api.TestSourceFileInfo(Path("my_file_1.py"), 1, 2))
    api.Test.discover(suite_1_test_2_id, source_file_info=None)
    api.Test.discover(
        suite_1_test_3_id,
        codeowners=["@romain", "@romain2"],
        source_file_info=api.TestSourceFileInfo(Path("my_file_1.py"), 4, 12),
        is_early_flake_detection=True,
    )

    module_2_id = api.TestModuleId("module_2")
    suite_2_id = api.TestSuiteId(module_2_id, "suite_2")
    suite_2_test_1_id = api.TestId(suite_2_id, "test_1")
    suite_2_test_2_id = api.TestId(suite_2_id, "test_2")
    suite_2_test_3_id = api.TestId(suite_2_id, "test_3")

    api.TestModule.discover(module_2_id)
    api.TestSuite.discover(suite_2_id)
    api.Test.discover(suite_2_test_1_id, source_file_info=api.TestSourceFileInfo(Path("my_file_1.py"), 1, 2))
    api.Test.discover(suite_2_test_2_id, source_file_info=None)
    api.Test.discover(
        suite_2_test_3_id,
        codeowners=["@romain"],
        source_file_info=api.TestSourceFileInfo(Path("my_file_1.py"), 4, 12),
        is_early_flake_detection=True,
    )

    # END DISCOVERY

    api.TestSession.start()

    api.TestModule.start(module_1_id)

    api.TestSuite.start(suite_1_id)

    api.Test.start(suite_1_test_1_id)
    api.Test.finish(suite_1_test_1_id, api.TestStatus.FAIL, exc_info=_make_excinfo())
    api.Test.start(suite_1_test_2_id)
    api.Test.finish(suite_1_test_2_id, api.TestStatus.FAIL, exc_info=_make_excinfo())
    api.Test.start(suite_1_test_3_id)
    api.Test.finish(suite_1_test_3_id, api.TestStatus.FAIL, exc_info=_make_excinfo())

    api.TestSuite.finish(suite_1_id)

    api.TestModule.finish(module_1_id)

    api.TestModule.start(module_2_id)

    api.TestSuite.start(suite_2_id)

    api.Test.start(suite_2_test_1_id)
    api.Test.mark_fail(suite_2_test_1_id, exc_info=_make_excinfo())
    api.Test.start(suite_2_test_2_id)
    api.Test.mark_fail(suite_2_test_2_id, exc_info=_make_excinfo())
    api.Test.start(suite_2_test_3_id)
    api.Test.mark_fail(suite_2_test_3_id, exc_info=_make_excinfo())

    api.TestSuite.finish(suite_2_id)

    api.TestModule.finish(module_2_id)

    api.TestSession.finish()


if __name__ == "__main__":
    freeze_support()
    main()
