import dataclasses
from pathlib import Path

import pytest

from ddtrace.ext import test
from ddtrace.ext.test_visibility._test_visibility_base import TestId
from ddtrace.ext.test_visibility._test_visibility_base import TestModuleId
from ddtrace.ext.test_visibility._test_visibility_base import TestSuiteId
from ddtrace.ext.test_visibility.api import TestSourceFileInfo
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._suite import TestVisibilitySuite
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS


@pytest.fixture
def civisibility_settings(tracer):
    return TestVisibilitySessionSettings(
        tracer=tracer,
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
    def test_civisibilityitem_enforces_sourcefile_info_on_tests(self, civisibility_settings):
        ci_test = TestVisibilityTest(
            _get_default_test_id().name,
            civisibility_settings,
            source_file_info=_get_good_test_source_file_info(),
        )
        assert ci_test._source_file_info.path == Path("/absolute/path/to/my_file_name")
        assert ci_test._source_file_info.start_line == 1
        assert ci_test._source_file_info.end_line == 2

    def test_civiisibilityitem_enforces_sourcefile_info_on_suites(self, civisibility_settings):
        ci_suite = TestVisibilitySuite(
            _get_default_suite_id().name,
            civisibility_settings,
            source_file_info=_get_good_suite_source_file_info(),
        )
        assert ci_suite._source_file_info.path == Path("/absolute/path/to/my_file_name")
        assert ci_suite._source_file_info.start_line is None
        assert ci_suite._source_file_info.end_line is None

    @pytest.mark.parametrize(
        "item_cls,item_name",
        [(TestVisibilitySuite, _get_default_suite_id().name), (TestVisibilityTest, _get_default_test_id().name)],
    )
    @pytest.mark.parametrize("itr_enabled", [True, False])
    @pytest.mark.parametrize("itr_test_skipping_enabled", [True, False])
    def test_civisibilityitem_sets_itr_test_skipping_enabled_tag_on_suite_and_test(
        self, civisibility_settings, item_cls, item_name, itr_enabled, itr_test_skipping_enabled
    ):
        settings = dataclasses.replace(
            civisibility_settings,
            itr_enabled=itr_enabled,
            itr_test_skipping_enabled=itr_test_skipping_enabled,
        )
        ci_item = item_cls(item_name, settings)

        ci_item._set_itr_tags(itr_enabled)

        assert ci_item.get_tag(test.ITR_TEST_SKIPPING_ENABLED) is itr_test_skipping_enabled


class TestCIVisibilitySessionSettings:
    def test_civisibility_sessionsettings_root_dir_accepts_absolute_path(self, civisibility_settings):
        assert civisibility_settings.workspace_path.is_absolute()

    def test_civisibility_sessionsettings_root_dir_rejects_relative_path(self, tracer):
        with pytest.raises(ValueError):
            _ = TestVisibilitySessionSettings(
                tracer=tracer,
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

    def test_civisibility_sessionsettings_root_dir_rejects_non_path(self, tracer):
        with pytest.raises(TypeError):
            _ = TestVisibilitySessionSettings(
                tracer=tracer,
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


@pytest.mark.parametrize("consumer", ["module", "root-module", "source", "coverage"])
@pytest.mark.parametrize("root_is_alias", [False, True])
def test_legacy_checkout_symlink_paths(tmp_path, civisibility_settings, consumer, root_is_alias):
    from ddtrace.internal.ci_visibility.api._coverage_data import TestVisibilityCoverageData
    from ddtrace.internal.ci_visibility.api._module import TestVisibilityModule
    from ddtrace.internal.coverage.coverage_lines import CoverageLines

    checkout = tmp_path / "checkout"
    (checkout / "tests").mkdir(parents=True)
    (checkout / "tests/test_example.py").write_text("def test_example(): pass\n")
    alias = tmp_path / "workspace"
    alias.symlink_to(checkout, target_is_directory=True)
    workspace = alias if root_is_alias else checkout
    collected_root = checkout if root_is_alias else alias
    source = collected_root / "tests/test_example.py"
    settings = dataclasses.replace(civisibility_settings, workspace_path=workspace)

    if consumer in ("module", "root-module"):
        module_path = collected_root if consumer == "root-module" else source.parent
        module = TestVisibilityModule("tests", settings, module_path=module_path)
        assert module._get_hierarchy_tags()[test.MODULE_PATH] == ("" if consumer == "root-module" else "tests")
    elif consumer == "source":
        item = TestVisibilityTest("test_example", settings, source_file_info=TestSourceFileInfo(source, 3, 4))
        item._set_default_tags()
        assert item.get_tag(test.SOURCE_FILE) == "tests/test_example.py"
    else:
        lines = CoverageLines.from_list([1, 3])
        coverage = TestVisibilityCoverageData()
        coverage.add_covered_files({source: lines})
        assert coverage.build_payload(workspace) == {
            "files": [{"filename": "/tests/test_example.py", "bitmap": lines.to_bytes()}]
        }
