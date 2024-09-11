import subprocess
import textwrap

import pytest

from tests.ci_visibility.util import _get_default_ci_env_vars
from tests.utils import TracerTestCase
from tests.utils import override_env
from tests.utils import snapshot


SNAPSHOT_IGNORES = [
    "meta.error.stack",
    "meta.library_version",
    "meta.os.architecture",
    "meta.os.platform",
    "meta.os.version",
    "meta.runtime-id",
    "meta.runtime.version",
    "meta.test.framework_version",
    "meta.test_module_id",
    "meta.test_session_id",
    "meta.test_suite_id",
    "metrics._dd.top_level",
    "metrics._dd.tracer_kr",
    "metrics._sampling_priority_v1",
    "metrics.process_id",
    "duration",
    "start",
]

SNAPSHOT_IGNORES_ITR_COVERAGE = ["metrics.test.source.start", "metrics.test.source.end", "meta.test.source.file"]

SNAPSHOT_IGNORES_GITLAB = [
    "meta._dd.ci.env_vars",
    "meta.ci.job.name",
    "meta.ci.job.url",
    "meta.ci.node.labels",
    "meta.ci.node.name",
    "meta.ci.pipeline.id",
    "meta.ci.pipeline.name",
    "meta.ci.pipeline.number",
    "meta.ci.pipeline.url",
    "meta.ci.provider.name",
    "meta.ci.stage.name",
    "meta.ci.workspace_path",
    "meta.git.branch",
    "meta.git.commit.author.date",
    "meta.git.commit.author.email",
    "meta.git.commit.author.name",
    "meta.git.commit.message",
    "meta.git.commit.sha",
    "meta.git.repository_url",
]


class UnittestSnapshotTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_GITLAB)
    def test_unittest_generates_source_file_data(self):
        ret_false = """
        def ret_false():
            return False
        """
        self.testdir.makepyfile(ret_false=ret_false)
        lib_fn = """
        def lib_fn():
            return True
        """
        self.testdir.makepyfile(lib_fn=lib_fn)
        test_source_file = """
        import unittest

        class CoverageTestCase(unittest.TestCase):
            def test_first(self):
                from lib_fn import lib_fn
                assert lib_fn()
            def test_second(self):
                from ret_false import ret_false
                assert not ret_false()
            def test_third(self):
                from ret_false import ret_false
                assert ret_false()
        """
        self.testdir.makepyfile(test_source_file=test_source_file)
        self.testdir.chdir()
        subprocess.run(
            ["ddtrace-run", "python", "-m", "unittest"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_get_default_ci_env_vars(dict(DD_API_KEY="foobar.baz", DD_CIVISIBILITY_AGENTLESS_ENABLED="false")),
        )

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_ITR_COVERAGE + SNAPSHOT_IGNORES_GITLAB)
    def test_unittest_generates_coverage_correctly(self):
        ret_false = """
        def ret_false():
            return False
        """
        self.testdir.makepyfile(ret_false=ret_false)
        lib_fn = """
        def lib_fn():
            return True
        """
        self.testdir.makepyfile(lib_fn=lib_fn)
        test_my_coverage = """
        import unittest
        import ddtrace
        from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
        from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
            return_value=_CIVisibilitySettings(True, False, False, True)
        )
        class CoverageTestCase(unittest.TestCase):
            def test_cov(self):
                from lib_fn import lib_fn
                assert lib_fn()
            def test_second(self):
                from ret_false import ret_false
                assert not ret_false()
        """
        self.testdir.makepyfile(test_my_coverage=test_my_coverage)
        self.testdir.chdir()
        subprocess.run(
            ["ddtrace-run", "python", "-m", "unittest"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_get_default_ci_env_vars(
                dict(
                    DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1", DD_CIVISIBILITY_AGENTLESS_ENABLED="false"
                )
            ),
        )

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_ITR_COVERAGE + SNAPSHOT_IGNORES_GITLAB)
    def test_unittest_generates_coverage_correctly_with_skipped(self):
        ret_false = """
        def ret_false():
            return False
        """
        self.testdir.makepyfile(ret_false=ret_false)
        lib_fn = """
        def lib_fn():
            return True
        """
        self.testdir.makepyfile(lib_fn=lib_fn)
        test_my_coverage = """
        import unittest
        import ddtrace
        from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
        from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
            return_value=_CIVisibilitySettings(True, False, False, True)
        )
        class CoverageTestCase(unittest.TestCase):
            def test_cov(self):
                from lib_fn import lib_fn
                assert lib_fn()
            @unittest.skip(reason="this should not have coverage")
            def test_second(self):
                from ret_false import ret_false
                assert not ret_false()
        """
        self.testdir.makepyfile(test_my_coverage=test_my_coverage)
        self.testdir.chdir()
        with override_env(
            _get_default_ci_env_vars(
                dict(
                    DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1", DD_CIVISIBILITY_AGENTLESS_ENABLED="false"
                )
            ),
            replace_os_env=True,
        ):
            subprocess.run(["ddtrace-run", "python", "-m", "unittest"])

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_ITR_COVERAGE + SNAPSHOT_IGNORES_GITLAB)
    def test_unittest_will_report_coverage_by_test_with_itr_skipped(self):
        ret_false = """
        def ret_false():
            return False
        """
        self.testdir.makepyfile(ret_false=ret_false)
        lib_fn = """
        def lib_fn():
            return True
        """
        self.testdir.makepyfile(lib_fn=lib_fn)
        test_my_coverage = textwrap.dedent(
            """
            import unittest
            import ddtrace
            from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
            from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
            from ddtrace.internal.ci_visibility.constants import ITR_CORRELATION_ID_TAG_NAME

            from unittest.mock import Mock
            ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
                return_value=_CIVisibilitySettings(True, True, False, True)
            )

            def mock_fetch_tests_to_skip_side_effect(_):
                _CIVisibility._instance._itr_meta.update({ITR_CORRELATION_ID_TAG_NAME: "unittestcorrelationid"})
                _CIVisibility._instance._tests_to_skip = {
                    "test_my_coverage/CoverageTestCase": "test_cov"
                }

            ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip = Mock(
                side_effect=mock_fetch_tests_to_skip_side_effect
            )

            class CoverageTestCase(unittest.TestCase):
                def test_cov(self):
                    from lib_fn import lib_fn
                    assert lib_fn()
                def test_second(self):
                    from ret_false import ret_false
                    assert not ret_false()
            """
        )
        self.testdir.makepyfile(test_my_coverage=test_my_coverage)
        self.testdir.chdir()
        subprocess.run(
            ["ddtrace-run", "python", "-m", "unittest"],
            env=_get_default_ci_env_vars(
                dict(
                    DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1", DD_CIVISIBILITY_AGENTLESS_ENABLED="false"
                )
            ),
        )

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_ITR_COVERAGE + SNAPSHOT_IGNORES_GITLAB)
    def test_unittest_will_report_coverage_by_test_with_itr_skipped_multiple(self):
        ret_false = """
        def ret_false():
            return False
        """
        self.testdir.makepyfile(ret_false=ret_false)
        lib_fn = """
        def lib_fn():
            return True
        """
        self.testdir.makepyfile(lib_fn=lib_fn)
        test_my_coverage = textwrap.dedent(
            """
            import unittest
            import ddtrace
            from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
            from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
            from ddtrace.internal.ci_visibility.constants import ITR_CORRELATION_ID_TAG_NAME

            from unittest.mock import Mock
            ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
                return_value=_CIVisibilitySettings(True, True, False, True)
            )

            def mock_fetch_tests_to_skip_side_effect(_):
                _CIVisibility._instance._itr_meta.update({ITR_CORRELATION_ID_TAG_NAME: "unittestcorrelationid"})
                _CIVisibility._instance._tests_to_skip = {
                "test_my_coverage/CoverageTestCase": ["test_cov", "test_second"],
            }

            ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip = Mock(
                side_effect=mock_fetch_tests_to_skip_side_effect
            )

            class CoverageTestCase(unittest.TestCase):
                def test_cov(self):
                    from lib_fn import lib_fn
                    assert lib_fn()
                def test_second(self):
                    from ret_false import ret_false
                    assert not ret_false()
            """
        )
        self.testdir.makepyfile(test_my_coverage=test_my_coverage)
        self.testdir.chdir()
        subprocess.run(
            ["ddtrace-run", "python", "-m", "unittest"],
            env=_get_default_ci_env_vars(
                dict(
                    DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1", DD_CIVISIBILITY_AGENTLESS_ENABLED="false"
                )
            ),
        )

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_ITR_COVERAGE + SNAPSHOT_IGNORES_GITLAB)
    def test_unittest_will_force_run_unskippable_tests(self):
        ret_false = """
        def ret_false():
            return False
        """
        self.testdir.makepyfile(ret_false=ret_false)
        lib_fn = """
        def lib_fn():
            return True
        """
        self.testdir.makepyfile(lib_fn=lib_fn)
        test_my_coverage = """
        import unittest
        import ddtrace
        from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
        from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
            return_value=_CIVisibilitySettings(True, True, False, True)
        )
        ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip = Mock()
        ddtrace.internal.ci_visibility.recorder.CIVisibility._tests_to_skip = {
            "test_my_coverage/CoverageTestCase": ["test_cov"],
        }
        class CoverageTestCase(unittest.TestCase):
            @unittest.skipIf(False, reason="datadog_itr_unskippable")
            def test_cov(self):
                from lib_fn import lib_fn
                assert lib_fn()
            def test_second(self):
                from ret_false import ret_false
                assert not ret_false()
        """
        self.testdir.makepyfile(test_my_coverage=test_my_coverage)
        self.testdir.chdir()
        subprocess.run(
            ["ddtrace-run", "python", "-m", "unittest"],
            env=_get_default_ci_env_vars(
                dict(
                    DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1", DD_CIVISIBILITY_AGENTLESS_ENABLED="false"
                )
            ),
        )

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_ITR_COVERAGE + SNAPSHOT_IGNORES_GITLAB)
    def test_unittest_will_force_run_multiple_unskippable_tests(self):
        ret_false = """
        def ret_false():
            return False
        """
        self.testdir.makepyfile(ret_false=ret_false)
        lib_fn = """
        def lib_fn():
            return True
        """
        self.testdir.makepyfile(lib_fn=lib_fn)
        test_my_coverage = """
        import unittest
        import ddtrace
        from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
        from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
            return_value=_CIVisibilitySettings(True, True, False, True)
        )
        ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip = Mock()
        ddtrace.internal.ci_visibility.recorder.CIVisibility._tests_to_skip = {
            "test_my_coverage/CoverageTestCase": ["test_cov", "test_second"],
        }
        class CoverageTestCase(unittest.TestCase):
            @unittest.skipIf(False, reason="datadog_itr_unskippable")
            def test_cov(self):
                from lib_fn import lib_fn
                assert lib_fn()
            @unittest.skipIf(False, reason="datadog_itr_unskippable")
            def test_second(self):
                from ret_false import ret_false
                assert not ret_false()
            def test_third(self):
                from ret_false import ret_false
                assert not ret_false()
        """
        self.testdir.makepyfile(test_my_coverage=test_my_coverage)
        self.testdir.chdir()
        subprocess.run(
            ["ddtrace-run", "python", "-m", "unittest"],
            env=_get_default_ci_env_vars(
                dict(
                    DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1", DD_CIVISIBILITY_AGENTLESS_ENABLED="false"
                )
            ),
        )

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_ITR_COVERAGE + SNAPSHOT_IGNORES_GITLAB)
    def test_unittest_will_skip_invalid_unskippable_tests(self):
        ret_false = """
        def ret_false():
            return False
        """
        self.testdir.makepyfile(ret_false=ret_false)
        lib_fn = """
        def lib_fn():
            return True
        """
        self.testdir.makepyfile(lib_fn=lib_fn)
        test_my_coverage = """
        import unittest
        import ddtrace
        from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
        from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
            return_value=_CIVisibilitySettings(True, True, False, True)
        )
        ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip = Mock()
        ddtrace.internal.ci_visibility.recorder.CIVisibility._tests_to_skip = {
            "test_my_coverage/CoverageTestCase": ["test_cov", "test_third"],
        }
        class CoverageTestCase(unittest.TestCase):
            @unittest.skipIf(True, reason="datadog_itr_unskippable")
            def test_cov(self):
                from lib_fn import lib_fn
                assert lib_fn()
            @unittest.skipIf(True, reason="datadog_itr_unskippable")
            def test_second(self):
                from ret_false import ret_false
                assert not ret_false()
            @unittest.skip(reason="datadog_itr_unskippable")
            def test_third(self):
                from ret_false import ret_false
                assert not ret_false()
            @unittest.skip(reason="datadog_itr_unskippable")
            def test_fourth(self):
                from ret_false import ret_false
                assert not ret_false()
        """
        self.testdir.makepyfile(test_my_coverage=test_my_coverage)
        self.testdir.chdir()
        subprocess.run(
            ["ddtrace-run", "python", "-m", "unittest"],
            env=_get_default_ci_env_vars(
                dict(
                    DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1", DD_CIVISIBILITY_AGENTLESS_ENABLED="false"
                )
            ),
        )

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_ITR_COVERAGE + SNAPSHOT_IGNORES_GITLAB)
    def test_unittest_will_skip_unskippable_test_if_skip_decorator(self):
        ret_false = """
            def ret_false():
                return False
            """
        self.testdir.makepyfile(ret_false=ret_false)
        lib_fn = """
            def lib_fn():
                return True
            """
        self.testdir.makepyfile(lib_fn=lib_fn)
        test_my_coverage = """
            import unittest
            import ddtrace
            from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
            from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
            from unittest import mock
            from unittest.mock import Mock
            ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
                return_value=_CIVisibilitySettings(True, True, False, True)
            )
            ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip = Mock()
            ddtrace.internal.ci_visibility.recorder.CIVisibility._tests_to_skip = {
                "test_my_coverage/CoverageTestCase": ["test_cov", "test_third"],
            }
            class CoverageTestCase(unittest.TestCase):
                @unittest.skip(reason="something else")
                @unittest.skipIf(False, reason="datadog_itr_unskippable")
                def test_cov(self):
                    from lib_fn import lib_fn
                    assert lib_fn()
                @unittest.skip(reason="something else")
                @unittest.skipIf(False, reason="datadog_itr_unskippable")
                def test_second(self):
                    from ret_false import ret_false
                    assert not ret_false()
                @unittest.skip(reason="datadog_itr_unskippable")
                @unittest.skipIf(False, reason="datadog_itr_unskippable")
                def test_third(self):
                    from ret_false import ret_false
                    assert not ret_false()
                @unittest.skip(reason="datadog_itr_unskippable")
                @unittest.skipIf(False, reason="datadog_itr_unskippable")
                def test_fourth(self):
                    from ret_false import ret_false
                    assert not ret_false()
            """
        self.testdir.makepyfile(test_my_coverage=test_my_coverage)
        self.testdir.chdir()
        subprocess.run(
            ["ddtrace-run", "python", "-m", "unittest"],
            env=_get_default_ci_env_vars(
                dict(
                    DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1", DD_CIVISIBILITY_AGENTLESS_ENABLED="false"
                )
            ),
        )

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_ITR_COVERAGE + SNAPSHOT_IGNORES_GITLAB)
    def test_unittest_will_include_custom_tests(self):
        ret_false = """
        def ret_false():
            return False
        """
        self.testdir.makepyfile(ret_false=ret_false)
        lib_fn = """
        def lib_fn():
            return True
        """
        self.testdir.makepyfile(lib_fn=lib_fn)
        test_my_coverage = """
        import unittest
        import ddtrace
        from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
        from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
            return_value=_CIVisibilitySettings(True, True, False, True)
        )
        ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip = Mock()
        ddtrace.internal.ci_visibility.recorder.CIVisibility._tests_to_skip = {
            "test_my_coverage/CoverageTestCase": ["test_cov"],
        }
        class CoverageTestCase(unittest.TestCase):
            @unittest.skipIf(False, reason="datadog_itr_unskippable")
            def test_cov(self):
                from lib_fn import lib_fn
                assert lib_fn()
            def test_second(self):
                from ret_false import ret_false
                assert not ret_false()
        """
        self.testdir.makepyfile(test_my_coverage=test_my_coverage)
        self.testdir.chdir()
        subprocess.run(
            ["ddtrace-run", "python", "-m", "unittest"],
            env=_get_default_ci_env_vars(
                dict(
                    DD_API_KEY="foobar.baz",
                    DD_CIVISIBILITY_ITR_ENABLED="1",
                    DD_CIVISIBILITY_AGENTLESS_ENABLED="false",
                    DD_TAGS="test.configuration.custom_key:some_value",
                )
            ),
        )

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_ITR_COVERAGE + SNAPSHOT_IGNORES_GITLAB)
    def test_unittest_will_include_lines_pct(self):
        tools = """
        def add_two_number_list(list_1, list_2):
            output_list = []
            for number_a, number_b in zip(list_1, list_2):
                output_list.append(number_a + number_b)
            return output_list

        def multiply_two_number_list(list_1, list_2):
            output_list = []
            for number_a, number_b in zip(list_1, list_2):
                output_list.append(number_a * number_b)
            return output_list
        """
        self.testdir.makepyfile(tools=tools)
        test_tools = """
        import unittest
        from tools import add_two_number_list

        class CodeCoverageTestCase(unittest.TestCase):
            def test_add_two_number_list(self):
                a_list = [1,2,3,4,5,6,7,8]
                b_list = [2,3,4,5,6,7,8,9]
                actual_output = add_two_number_list(a_list, b_list)

                assert actual_output == [3,5,7,9,11,13,15,17]
        """
        self.testdir.makepyfile(test_tools=test_tools)
        self.testdir.chdir()
        subprocess.run(
            ["ddtrace-run", "coverage", "run", "--include=tools.py", "-m", "unittest"],
            env=_get_default_ci_env_vars(
                dict(
                    DD_API_KEY="foobar.baz",
                    DD_PATCH_MODULES="sqlite3:false",
                    DD_CIVISIBILITY_AGENTLESS_ENABLED="false",
                )
            ),
        )
