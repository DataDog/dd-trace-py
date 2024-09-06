from pathlib import Path

import pytest

from ddtrace.ext.test_visibility._item_ids import TestId
from ddtrace.ext.test_visibility._item_ids import TestModuleId
from ddtrace.ext.test_visibility._item_ids import TestSuiteId
from ddtrace.ext.test_visibility.api import TestSourceFileInfo
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._suite import TestVisibilitySuite
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from tests.utils import DummyTracer


def _get_default_civisibility_settings():
    return TestVisibilitySessionSettings(
        tracer=DummyTracer(),
        test_service="test_service",
        test_command="test_command",
        test_framework="test_framework",
        test_framework_version="1.2.3",
        test_framework_metric_name=TEST_FRAMEWORKS.MANUAL,
        session_operation_name="session_operation_name",
        module_operation_name="module_operation_name",
        suite_operation_name="suite_operation_name",
        test_operation_name="test_operation_name",
        workspace_path=Path("/absolute/path/to/root_dir"),
    )


def _get_default_module_id():
    return TestModuleId("module_name")


def _get_default_suite_id():
    return TestSuiteId(_get_default_module_id(), "suite_name")


def _get_default_test_id():
    return TestId(_get_default_suite_id(), "test_name")


def _get_good_test_source_file_info():
    return TestSourceFileInfo(Path("/absolute/path/to/my_file_name"), 1, 2)


def _get_bad_test_source_file_info():
    cisi = _get_good_test_source_file_info()
    object.__setattr__(cisi, "non/absolute/file_path", None)
    return cisi


def _get_good_suite_source_file_info():
    return TestSourceFileInfo(Path("/absolute/path/to/my_file_name"))


def _get_bad_suite_source_file_info():
    cisi = _get_good_suite_source_file_info()
    object.__setattr__(cisi, "non/absolute/file_path", None)
    return cisi


class TestCIVisibilityItems:
    def test_civisibilityitem_enforces_sourcefile_info_on_tests(self):
        ci_test = TestVisibilityTest(
            _get_default_test_id().name,
            _get_default_civisibility_settings(),
            source_file_info=_get_good_test_source_file_info(),
        )
        assert ci_test._source_file_info.path == Path("/absolute/path/to/my_file_name")
        assert ci_test._source_file_info.start_line == 1
        assert ci_test._source_file_info.end_line == 2

    def test_civiisibilityitem_enforces_sourcefile_info_on_suites(self):
        ci_suite = TestVisibilitySuite(
            _get_default_suite_id().name,
            _get_default_civisibility_settings(),
            source_file_info=_get_good_suite_source_file_info(),
        )
        assert ci_suite._source_file_info.path == Path("/absolute/path/to/my_file_name")
        assert ci_suite._source_file_info.start_line is None
        assert ci_suite._source_file_info.end_line is None


class TestCIVisibilitySessionSettings:
    def test_civisibility_sessionsettings_root_dir_accepts_absolute_path(self):
        settings = _get_default_civisibility_settings()
        assert settings.workspace_path.is_absolute()

    def test_civisibility_sessionsettings_root_dir_rejects_relative_path(self):
        with pytest.raises(ValueError):
            _ = TestVisibilitySessionSettings(
                tracer=DummyTracer(),
                test_service="test_service",
                test_command="test_command",
                test_framework="test_framework",
                test_framework_metric_name=TEST_FRAMEWORKS.MANUAL,
                test_framework_version="1.2.3",
                session_operation_name="session_operation_name",
                module_operation_name="module_operation_name",
                suite_operation_name="suite_operation_name",
                test_operation_name="test_operation_name",
                workspace_path=Path("relative/path/to/root_dir"),
            )

    def test_civisibility_sessionsettings_root_dir_rejects_non_path(self):
        with pytest.raises(TypeError):
            _ = TestVisibilitySessionSettings(
                tracer=DummyTracer(),
                test_service="test_service",
                test_command="test_command",
                test_framework="test_framework",
                test_framework_metric_name=TEST_FRAMEWORKS.MANUAL,
                test_framework_version="1.2.3",
                session_operation_name="session_operation_name",
                module_operation_name="module_operation_name",
                suite_operation_name="suite_operation_name",
                test_operation_name="test_operation_name",
                workspace_path="not_even_a_path",
            )

    def test_civisibility_sessionsettings_rejects_non_tracer(self):
        with pytest.raises(TypeError):
            _ = TestVisibilitySessionSettings(
                tracer="not a tracer",
                test_service="test_service",
                test_command="test_command",
                test_framework="test_framework",
                test_framework_metric_name=TEST_FRAMEWORKS.MANUAL,
                test_framework_version="1.2.3",
                session_operation_name="session_operation_name",
                module_operation_name="module_operation_name",
                suite_operation_name="suite_operation_name",
                test_operation_name="test_operation_name",
                workspace_path=Path("/absolute/path/to/root_dir"),
            )
