import ddtrace.ext.test_visibility.api as ext_api
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.test_visibility import api
from tests.ci_visibility.util import set_up_mock_civisibility


class TestCIITRMixin:
    """Tests whether skippable tests and suites are correctly identified

    Note: these tests do not bother discovering a session as the ITR functionality currently does not rely on sessions.
    """

    def test_api_is_item_itr_skippable_test_level(self):
        with set_up_mock_civisibility(
            itr_enabled=True,
            skipping_enabled=True,
            suite_skipping_mode=False,
            skippable_items={
                "skippable_module/suite.py": ["skippable_test"],
            },
        ):
            CIVisibility.enable()

            assert CIVisibility.enabled is True
            assert CIVisibility._instance._suite_skipping_mode is False

            skippable_module_id = ext_api.TestModuleId("skippable_module")

            skippable_suite_id = ext_api.TestSuiteId(skippable_module_id, "suite.py")
            skippable_test_id = ext_api.TestId(skippable_suite_id, "skippable_test")
            non_skippable_test_id = ext_api.TestId(skippable_suite_id, "non_skippable_test")

            non_skippable_suite_id = ext_api.TestSuiteId(skippable_module_id, "non_skippable_suite.py")
            non_skippable_suite_skippable_test_id = ext_api.TestId(non_skippable_suite_id, "skippable_test")

            assert api.InternalTest.is_itr_skippable(skippable_test_id) is True
            assert api.InternalTest.is_itr_skippable(non_skippable_test_id) is False
            assert api.InternalTest.is_itr_skippable(non_skippable_suite_skippable_test_id) is False

            CIVisibility.disable()

    def test_api_is_item_itr_skippable_false_when_skipping_disabled_test_level(self):
        with set_up_mock_civisibility(
            itr_enabled=True,
            skipping_enabled=False,
            suite_skipping_mode=False,
            skippable_items={
                "skippable_module/suite.py": ["skippable_test"],
            },
        ):
            CIVisibility.enable()

            assert CIVisibility.enabled is True
            assert CIVisibility._instance._suite_skipping_mode is False

            skippable_module_id = ext_api.TestModuleId("skippable_module")

            skippable_suite_id = ext_api.TestSuiteId(skippable_module_id, "suite.py")
            skippable_test_id = ext_api.TestId(skippable_suite_id, "skippable_test")
            non_skippable_test_id = ext_api.TestId(skippable_suite_id, "non_skippable_test")

            non_skippable_suite_id = ext_api.TestSuiteId(skippable_module_id, "non_skippable_suite.py")
            non_skippable_suite_skippable_test_id = ext_api.TestId(non_skippable_suite_id, "skippable_test")

            assert api.InternalTest.is_itr_skippable(skippable_test_id) is False
            assert api.InternalTest.is_itr_skippable(non_skippable_test_id) is False
            assert api.InternalTest.is_itr_skippable(non_skippable_suite_skippable_test_id) is False

            CIVisibility.disable()

    def test_api_is_item_itr_skippable_suite_level(self):
        with set_up_mock_civisibility(
            itr_enabled=True,
            skipping_enabled=True,
            suite_skipping_mode=True,
            skippable_items=["skippable_module/skippable_suite.py"],
        ):
            CIVisibility.enable()

            assert CIVisibility.enabled is True
            assert CIVisibility._instance._suite_skipping_mode is True

            skippable_module_id = ext_api.TestModuleId("skippable_module")
            skippable_suite_id = ext_api.TestSuiteId(skippable_module_id, "skippable_suite.py")
            non_skippable_suite_id = ext_api.TestSuiteId(skippable_module_id, "non_skippable_suite.py")

            non_skippable_module_id = ext_api.TestModuleId("non_skippable_module")
            non_skippable_module_skippable_suite_id = ext_api.TestSuiteId(non_skippable_module_id, "skippable_suite.py")

            assert api.InternalTestSuite.is_itr_skippable(skippable_suite_id) is True
            assert api.InternalTestSuite.is_itr_skippable(non_skippable_suite_id) is False
            assert api.InternalTestSuite.is_itr_skippable(non_skippable_module_skippable_suite_id) is False

            CIVisibility.disable()

    def test_api_is_item_itr_skippable_false_when_skipping_disabled_suite_level(self):
        with set_up_mock_civisibility(
            itr_enabled=True,
            skipping_enabled=False,
            suite_skipping_mode=True,
            skippable_items=["skippable_module/skippable_suite.py"],
        ):
            CIVisibility.enable()

            assert CIVisibility.enabled is True
            assert CIVisibility._instance._suite_skipping_mode is True

            skippable_module_id = ext_api.TestModuleId("skippable_module")
            skippable_suite_id = ext_api.TestSuiteId(skippable_module_id, "skippable_suite.py")
            non_skippable_suite_id = ext_api.TestSuiteId(skippable_module_id, "non_skippable_suite.py")

            non_skippable_module_id = ext_api.TestModuleId("non_skippable_module")
            non_skippable_module_skippable_suite_id = ext_api.TestSuiteId(non_skippable_module_id, "skippable_suite.py")

            assert api.InternalTestSuite.is_itr_skippable(skippable_suite_id) is False
            assert api.InternalTestSuite.is_itr_skippable(non_skippable_suite_id) is False
            assert api.InternalTestSuite.is_itr_skippable(non_skippable_module_skippable_suite_id) is False

            CIVisibility.disable()
