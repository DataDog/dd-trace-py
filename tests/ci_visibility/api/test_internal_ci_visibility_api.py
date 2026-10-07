from concurrent.futures import ThreadPoolExecutor
import dataclasses
from pathlib import Path

import msgpack
import pytest

from ddtrace.ext import test
from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.ext.test_visibility._test_visibility_base import TestId
from ddtrace.ext.test_visibility._test_visibility_base import TestModuleId
from ddtrace.ext.test_visibility._test_visibility_base import TestSuiteId
from ddtrace.ext.test_visibility.api import TestSourceFileInfo
from ddtrace.ext.test_visibility.status import TestStatus
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._module import TestVisibilityModule
from ddtrace.internal.ci_visibility.api._session import TestVisibilitySession
from ddtrace.internal.ci_visibility.api._suite import TestVisibilitySuite
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.encoder import CIVisibilityEncoderV01
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


class TestSuiteITRReporting:
    @staticmethod
    def encoded_content(suite):
        encoder = CIVisibilityEncoderV01(0, 0)
        encoder.put([suite.get_span()])
        [(payload, _)] = encoder.encode()
        [event] = msgpack.unpackb(payload, raw=False, strict_map_key=False)["events"]
        assert event["type"] == "test_suite_end"
        return event["content"]

    @pytest.mark.parametrize("coverage_enabled", [False, True])
    @pytest.mark.parametrize(
        "itr_enabled,skipping_enabled,outcomes,expected",
        [
            (True, True, ["itr", "itr", "skip", "pass"], 2),
            (True, True, ["skip", "skip"], 0),
            (True, True, ["pass"], 0),
            (True, True, [], 0),
            (True, False, ["skip", "pass"], 0),
            (True, False, [], 0),
            (False, False, ["skip", "pass"], None),
            (False, False, [], None),
            (True, True, ["forced", "unskippable", "disabled"], 0),
        ],
    )
    def test_serialized_suite(
        self, civisibility_settings, itr_enabled, skipping_enabled, outcomes, expected, coverage_enabled
    ):
        settings = dataclasses.replace(
            civisibility_settings,
            itr_enabled=itr_enabled,
            itr_test_skipping_enabled=skipping_enabled,
            itr_test_skipping_level=ITR_SKIPPING_LEVEL.TEST,
            coverage_enabled=coverage_enabled,
        )
        suite = TestVisibilitySuite("suite", settings)
        suite.start()
        for index, outcome in enumerate(outcomes):
            child = TestVisibilityTest(str(index), settings, is_disabled=outcome == "disabled")
            suite.add_child(TestId(_get_default_suite_id(), str(index)), child)
            child.start()
            if outcome == "itr":
                child.finish_itr_skipped()
            else:
                if outcome == "forced":
                    child.mark_itr_forced_run()
                elif outcome == "unskippable":
                    child.mark_itr_unskippable()
                child.finish_test(status=TestStatus.SKIP if outcome in ("skip", "disabled") else TestStatus.PASS)
                child.finish()
        suite.finish()
        content = self.encoded_content(suite)
        if expected is None:
            assert test.ITR_TEST_SKIPPING_COUNT not in content["metrics"]
            assert test.ITR_DD_CI_ITR_TESTS_SKIPPED not in content["meta"]
        else:
            count = content["metrics"][test.ITR_TEST_SKIPPING_COUNT]
            assert isinstance(count, (int, float)) and not isinstance(count, bool)
            assert count >= 0 and count == int(count) == expected
            assert content["meta"][test.ITR_DD_CI_ITR_TESTS_SKIPPED] == ("true" if expected else "false")

    @pytest.mark.parametrize("forced", [False, True])
    def test_suite_skipping_counts_suites(self, civisibility_settings, forced):
        settings = dataclasses.replace(
            civisibility_settings,
            itr_enabled=True,
            itr_test_skipping_enabled=True,
            itr_test_skipping_level=ITR_SKIPPING_LEVEL.SUITE,
        )
        suite = TestVisibilitySuite("suite", settings)
        suite.start()
        for index in range(3):
            child = TestVisibilityTest(str(index), settings)
            suite.add_child(TestId(_get_default_suite_id(), str(index)), child)
            child.start()
            if forced:
                child.mark_itr_unskippable()
                child.mark_itr_forced_run()
                child.finish_test(status=TestStatus.PASS)
                child.finish()
            else:
                child.finish_itr_skipped()
        if forced:
            suite.finish()
        else:
            suite.finish_itr_skipped()
        content = self.encoded_content(suite)
        assert content["metrics"][test.ITR_TEST_SKIPPING_COUNT] == (0 if forced else 1)
        assert content["meta"][test.ITR_DD_CI_ITR_TESTS_SKIPPED] == ("false" if forced else "true")

    @pytest.mark.parametrize("concurrent", [False, True])
    def test_suite_counters_and_session_total(self, civisibility_settings, concurrent):
        settings = dataclasses.replace(
            civisibility_settings,
            itr_enabled=True,
            itr_test_skipping_enabled=True,
            itr_test_skipping_level=ITR_SKIPPING_LEVEL.TEST,
        )
        session = TestVisibilitySession(settings)
        module = TestVisibilityModule("module", settings)
        module_id = TestModuleId("module")
        session.add_child(module_id, module)
        session.start()
        module.start()
        suites = [TestVisibilitySuite(name, settings) for name in ("A", "B")]
        factor = 100 if concurrent else 1
        children = []
        for suite, count in zip(suites, (2 * factor, factor)):
            suite_id = TestSuiteId(module_id, suite.name)
            module.add_child(suite_id, suite)
            suite.start()
            for index in range(count):
                child = TestVisibilityTest(str(index), settings)
                suite.add_child(TestId(suite_id, str(index)), child)
                child.start()
                children.append(child)
        if concurrent:
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda child: child.finish_itr_skipped(), children))
        else:
            for child in children:
                child.finish_itr_skipped()
        for suite in suites:
            suite.finish()
        module.finish()
        session.finish()
        for suite, expected in zip(suites, (2 * factor, factor)):
            content = self.encoded_content(suite)
            assert content["metrics"][test.ITR_TEST_SKIPPING_COUNT] == expected
            assert content["meta"][test.ITR_DD_CI_ITR_TESTS_SKIPPED] == "true"
        assert session.get_span().get_metric(test.ITR_TEST_SKIPPING_COUNT) == 3 * factor
