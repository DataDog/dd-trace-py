import subprocess

import pytest

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

SNAPSHOT_IGNORES_COVERAGE = ["metrics.test.source.start", "metrics.test.source.end", "meta.test.source.file"]


class UnittestSnapshotTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    @snapshot(ignores=SNAPSHOT_IGNORES)
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
        with override_env(dict(DD_API_KEY="foobar.baz")):
            subprocess.run(
                ["ddtrace-run", "python", "-m", "unittest"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_COVERAGE)
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
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(return_value=(True, False))
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
        with override_env(dict(DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1")):
            subprocess.run(
                ["ddtrace-run", "python", "-m", "unittest"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_COVERAGE)
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
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(return_value=(True, False))
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
        with override_env(dict(DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1")):
            subprocess.run(["ddtrace-run", "python", "-m", "unittest"])

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_COVERAGE)
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
        test_my_coverage = """
                import unittest
        import ddtrace
        from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
            return_value=(True, True)
        )
        ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip = Mock()
        ddtrace.internal.ci_visibility.recorder.CIVisibility._tests_to_skip = {
            "test_my_coverage/CoverageTestCase": "test_cov"
        }
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
        with override_env(dict(DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1")):
            subprocess.run(["ddtrace-run", "python", "-m", "unittest"])

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_COVERAGE)
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
        test_my_coverage = """
        import unittest
        import ddtrace
        from ddtrace.internal.ci_visibility import CIVisibility as _CIVisibility
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
            return_value=(True, True)
        )
        ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip = Mock()
        ddtrace.internal.ci_visibility.recorder.CIVisibility._tests_to_skip = {
            "test_my_coverage/CoverageTestCase": ["test_cov", "test_second"],
        }
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
        with override_env(dict(DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1")):
            subprocess.run(["ddtrace-run", "python", "-m", "unittest"])

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_COVERAGE)
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
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
            return_value=(True, True)
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
        with override_env(dict(DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1")):
            subprocess.run(["ddtrace-run", "python", "-m", "unittest"])

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_COVERAGE)
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
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
            return_value=(True, True)
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
        with override_env(dict(DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1")):
            subprocess.run(["ddtrace-run", "python", "-m", "unittest"])

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_COVERAGE)
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
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
            return_value=(True, True)
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
        with override_env(dict(DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1")):
            subprocess.run(["ddtrace-run", "python", "-m", "unittest"])

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_COVERAGE)
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
            from unittest import mock
            from unittest.mock import Mock
            ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
                return_value=(True, True)
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
        with override_env(dict(DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1")):
            subprocess.run(["ddtrace-run", "python", "-m", "unittest"])

    @snapshot(ignores=SNAPSHOT_IGNORES + SNAPSHOT_IGNORES_COVERAGE)
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
        from unittest import mock
        from unittest.mock import Mock
        ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features = Mock(
            return_value=(True, True)
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
        with override_env(
            dict(
                DD_API_KEY="foobar.baz",
                DD_CIVISIBILITY_ITR_ENABLED="1",
                DD_TAGS="test.configuration.custom_key:some_value",
            )
        ):
            subprocess.run(["ddtrace-run", "python", "-m", "unittest"])
