from os import getcwd as os_getcwd
from pathlib import Path

import pytest

from ddtrace.ext.ci_visibility import api
from ddtrace.ext.ci_visibility.api import CISourceFileInfo
from ddtrace.internal.ci_visibility import CIVisibility
from tests.ci_visibility.util import set_up_mock_civisibility


class TestCISourceFileInfo:
    def test_source_file_info_happy_path(self):
        cisi = CISourceFileInfo(Path("/absolute/path/to/my_file_name"), 1, 2)
        assert cisi.path.is_absolute()
        assert cisi.path == Path("/absolute/path/to/my_file_name")

    def test_source_file_info_makes_path_absolute(self):
        """Should fail if the path is a string"""
        cisi = CISourceFileInfo(Path("my_file_name"), 3, 4)
        expected_path = Path(os_getcwd()) / "my_file_name"
        assert cisi.path.is_absolute()
        assert cisi.path == expected_path

    def test_source_file_info_enforces_path_type(self):
        with pytest.raises(ValueError):
            _ = CISourceFileInfo("my_file_name", 5, 6)

    def test_source_file_info_path_must_be_set(self):
        with pytest.raises(ValueError):
            _ = CISourceFileInfo(None, 5, 6)
        with pytest.raises(TypeError):
            _ = CISourceFileInfo(start_line=5, end_line=6)

    def test_source_file_info_enforces_lines_are_ints(self):
        with pytest.raises(ValueError):
            _ = CISourceFileInfo(Path("/absolute/path/my_file_name"), "5", 6)
        with pytest.raises(ValueError):
            _ = CISourceFileInfo(Path("/absolute/path/my_file_name"), 5, "6")
        with pytest.raises(ValueError):
            _ = CISourceFileInfo(Path("/absolute/path/my_file_name"), "5", "6")

    def test_source_file_info_enforces_lines_are_positive(self):
        with pytest.raises(ValueError):
            _ = CISourceFileInfo(Path("/absolute/path/my_file_name"), -1, 1)
        with pytest.raises(ValueError):
            _ = CISourceFileInfo(Path("/absolute/path/my_file_name"), 1, -1)
        with pytest.raises(ValueError):
            _ = CISourceFileInfo(Path("/absolute/path/my_file_name"), -1, -1)

    def test_source_file_info_enforces_start_line_less_than_end_line(self):
        with pytest.raises(ValueError):
            _ = CISourceFileInfo(Path("/absolute/path/my_file_name"), 2, 1)
        with pytest.raises(ValueError):
            _ = CISourceFileInfo(end_line=1, start_line=2, path=Path("/absolute/path/my_file_name"))
        with pytest.raises(ValueError):
            # start_line cannot be None if end_line is provided
            _ = CISourceFileInfo(Path("/absolute/path/my_file_name"), end_line=1)


class TestCIITRMixin:
    """Tests whether or not skippable tests and suites are correctly identified

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

            skippable_module_id = api.CIModuleId("skippable_module")

            skippable_suite_id = api.CISuiteId(skippable_module_id, "suite.py")
            skippable_test_id = api.CITestId(skippable_suite_id, "skippable_test")
            non_skippable_test_id = api.CITestId(skippable_suite_id, "non_skippable_test")

            non_skippable_suite_id = api.CISuiteId(skippable_module_id, "non_skippable_suite.py")
            non_skippable_suite_skippable_test_id = api.CITestId(non_skippable_suite_id, "skippable_test")

            assert api.CITest.is_item_itr_skippable(skippable_test_id) is True
            assert api.CITest.is_item_itr_skippable(non_skippable_test_id) is False
            assert api.CITest.is_item_itr_skippable(non_skippable_suite_skippable_test_id) is False

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

            skippable_module_id = api.CIModuleId("skippable_module")

            skippable_suite_id = api.CISuiteId(skippable_module_id, "suite.py")
            skippable_test_id = api.CITestId(skippable_suite_id, "skippable_test")
            non_skippable_test_id = api.CITestId(skippable_suite_id, "non_skippable_test")

            non_skippable_suite_id = api.CISuiteId(skippable_module_id, "non_skippable_suite.py")
            non_skippable_suite_skippable_test_id = api.CITestId(non_skippable_suite_id, "skippable_test")

            assert api.CITest.is_item_itr_skippable(skippable_test_id) is False
            assert api.CITest.is_item_itr_skippable(non_skippable_test_id) is False
            assert api.CITest.is_item_itr_skippable(non_skippable_suite_skippable_test_id) is False

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

            skippable_module_id = api.CIModuleId("skippable_module")
            skippable_suite_id = api.CISuiteId(skippable_module_id, "skippable_suite.py")
            non_skippable_suite_id = api.CISuiteId(skippable_module_id, "non_skippable_suite.py")

            non_skippable_module_id = api.CIModuleId("non_skippable_module")
            non_skippable_module_skippable_suite_id = api.CISuiteId(non_skippable_module_id, "skippable_suite.py")

            assert api.CISuite.is_item_itr_skippable(skippable_suite_id) is True
            assert api.CISuite.is_item_itr_skippable(non_skippable_suite_id) is False
            assert api.CISuite.is_item_itr_skippable(non_skippable_module_skippable_suite_id) is False

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

            skippable_module_id = api.CIModuleId("skippable_module")
            skippable_suite_id = api.CISuiteId(skippable_module_id, "skippable_suite.py")
            non_skippable_suite_id = api.CISuiteId(skippable_module_id, "non_skippable_suite.py")

            non_skippable_module_id = api.CIModuleId("non_skippable_module")
            non_skippable_module_skippable_suite_id = api.CISuiteId(non_skippable_module_id, "skippable_suite.py")

            assert api.CISuite.is_item_itr_skippable(skippable_suite_id) is False
            assert api.CISuite.is_item_itr_skippable(non_skippable_suite_id) is False
            assert api.CISuite.is_item_itr_skippable(non_skippable_module_skippable_suite_id) is False

            CIVisibility.disable()
