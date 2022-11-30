import json
import os
import sys

import mock
import pytest

from ddtrace import Pin
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.contrib.pytest.constants import XFAIL_REASON
from ddtrace.contrib.pytest.plugin import _extract_repository_name
from ddtrace.ext import ci
from ddtrace.ext import test
from tests.utils import TracerTestCase


class TestPytest(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch):
        self.testdir = testdir
        self.monkeypatch = monkeypatch

    def inline_run(self, *args):
        """Execute test script with test tracer."""

        class PinTracer:
            @staticmethod
            def pytest_configure(config):
                if Pin.get_from(config) is not None:
                    Pin.override(config, tracer=self.tracer)

        return self.testdir.inline_run(*args, plugins=[PinTracer()])

    def subprocess_run(self, *args):
        """Execute test script with test tracer."""
        return self.testdir.runpytest_subprocess(*args)

    @pytest.mark.skipif(sys.version_info[0] == 2, reason="Triggers a bug with coverage, sqlite and Python 2")
    def test_patch_all(self):
        """Test with --ddtrace-patch-all."""
        py_file = self.testdir.makepyfile(
            """
            import ddtrace

            def test_patched_all():
                assert ddtrace._monkey._PATCHED_MODULES
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace-patch-all", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 0

    @pytest.mark.skipif(sys.version_info[0] == 2, reason="Triggers a bug with coverage, sqlite and Python 2")
    def test_patch_all_init(self):
        """Test with ddtrace-patch-all via ini."""
        self.testdir.makefile(".ini", pytest="[pytest]\nddtrace-patch-all=1\n")
        py_file = self.testdir.makepyfile(
            """
            import ddtrace

            def test_patched_all():
                assert ddtrace._monkey._PATCHED_MODULES
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run(file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 0

    def test_disabled(self):
        """Test without --ddtrace."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            def test_no_trace(ddspan):
                assert ddspan is None
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run(file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 0

    def test_ini(self):
        """Test ini config."""
        self.testdir.makefile(".ini", pytest="[pytest]\nddtrace=1\n")
        py_file = self.testdir.makepyfile(
            """
            import pytest

            def test_ini(ddspan):
                assert ddspan is not None
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run(file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 1

    def test_parameterize_case(self):
        """Test parametrize case with simple objects."""
        py_file = self.testdir.makepyfile(
            """
            import pytest


            @pytest.mark.parametrize('item', [1, 2, 3, 4, pytest.param([1, 2, 3], marks=pytest.mark.skip)])
            class Test1(object):
                def test_1(self, item):
                    assert item in {1, 2, 3}
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=3, failed=1, skipped=1)
        spans = self.pop_spans()

        expected_params = [1, 2, 3, 4, [1, 2, 3]]
        assert len(spans) == 5
        for i in range(len(expected_params)):
            assert json.loads(spans[i].get_tag(test.PARAMETERS)) == {
                "arguments": {"item": str(expected_params[i])},
                "metadata": {},
            }

    def test_parameterize_case_complex_objects(self):
        """Test parametrize case with complex objects."""
        py_file = self.testdir.makepyfile(
            """
            from mock import MagicMock
            import pytest

            class A:
                def __init__(self, name, value):
                    self.name = name
                    self.value = value

            def item_param():
                return 42

            circular_reference = A("circular_reference", A("child", None))
            circular_reference.value.value = circular_reference

            @pytest.mark.parametrize(
            'item',
            [
                pytest.param(A("test_name", "value"), marks=pytest.mark.skip),
                pytest.param(A("test_name", A("inner_name", "value")), marks=pytest.mark.skip),
                pytest.param(item_param, marks=pytest.mark.skip),
                pytest.param({"a": A("test_name", "value"), "b": [1, 2, 3]}, marks=pytest.mark.skip),
                pytest.param(MagicMock(value=MagicMock()), marks=pytest.mark.skip),
                pytest.param(circular_reference, marks=pytest.mark.skip),
                pytest.param({("x", "y"): 12345}, marks=pytest.mark.skip)
            ]
            )
            class Test1(object):
                def test_1(self, item):
                    assert item in {1, 2, 3}
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(skipped=7)
        spans = self.pop_spans()

        # Since object will have arbitrary addresses, only need to ensure that
        # the params string contains most of the string representation of the object.
        expected_params_contains = [
            "test_parameterize_case_complex_objects.A",
            "test_parameterize_case_complex_objects.A",
            "<function item_param>",
            "'a': <test_parameterize_case_complex_objects.A",
            "<MagicMock id=",
            "test_parameterize_case_complex_objects.A",
            "{('x', 'y'): 12345}",
        ]
        assert len(spans) == 7
        for i in range(len(expected_params_contains)):
            assert expected_params_contains[i] in spans[i].get_tag(test.PARAMETERS)

    def test_parameterize_case_encoding_error(self):
        """Test parametrize case with complex objects that cannot be JSON encoded."""
        py_file = self.testdir.makepyfile(
            """
            from mock import MagicMock
            import pytest

            class A:
                def __repr__(self):
                    raise Exception("Cannot __repr__")

            @pytest.mark.parametrize('item',[A()])
            class Test1(object):
                def test_1(self, item):
                    assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 1
        assert json.loads(spans[0].get_tag(test.PARAMETERS)) == {
            "arguments": {"item": "Could not encode"},
            "metadata": {},
        }

    def test_skip(self):
        """Test skip case."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.skip(reason="decorator")
            def test_decorator():
                pass

            def test_body():
                pytest.skip("body")
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(skipped=2)
        spans = self.pop_spans()

        assert len(spans) == 2
        assert spans[0].get_tag(test.STATUS) == test.Status.SKIP.value
        assert spans[0].get_tag(test.SKIP_REASON) == "decorator"
        assert spans[1].get_tag(test.STATUS) == test.Status.SKIP.value
        assert spans[1].get_tag(test.SKIP_REASON) == "body"

    def test_skip_module_with_xfail_cases(self):
        """Test Xfail test cases for a module that is skipped entirely, which should be treated as skip tests."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            pytestmark = pytest.mark.skip(reason="reason")

            @pytest.mark.xfail(reason="XFail Case")
            def test_xfail():
                pass

            @pytest.mark.xfail(condition=False, reason="XFail Case")
            def test_xfail_conditional():
                pass
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(skipped=2)
        spans = self.pop_spans()

        assert len(spans) == 2
        assert spans[0].get_tag(test.STATUS) == test.Status.SKIP.value
        assert spans[0].get_tag(test.SKIP_REASON) == "reason"
        assert spans[1].get_tag(test.STATUS) == test.Status.SKIP.value
        assert spans[1].get_tag(test.SKIP_REASON) == "reason"

    def test_skipif_module(self):
        """Test XFail test cases for a module that is skipped entirely with the skipif marker."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            pytestmark = pytest.mark.skipif(True, reason="reason")

            @pytest.mark.xfail(reason="XFail")
            def test_xfail():
                pass

            @pytest.mark.xfail(condition=False, reason="XFail Case")
            def test_xfail_conditional():
                pass
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(skipped=2)
        spans = self.pop_spans()

        assert len(spans) == 2
        assert spans[0].get_tag(test.STATUS) == test.Status.SKIP.value
        assert spans[0].get_tag(test.SKIP_REASON) == "reason"
        assert spans[1].get_tag(test.STATUS) == test.Status.SKIP.value
        assert spans[1].get_tag(test.SKIP_REASON) == "reason"

    def test_xfail_fails(self):
        """Test xfail (expected failure) which fails, should be marked as pass."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.xfail(reason="test should fail")
            def test_should_fail():
                assert 0

            @pytest.mark.xfail(condition=True, reason="test should xfail")
            def test_xfail_conditional():
                assert 0
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        # pytest records xfail as skipped
        rec.assertoutcome(skipped=2)
        spans = self.pop_spans()

        assert len(spans) == 2
        assert spans[0].get_tag(test.STATUS) == test.Status.PASS.value
        assert spans[0].get_tag(test.RESULT) == test.Status.XFAIL.value
        assert spans[0].get_tag(XFAIL_REASON) == "test should fail"
        assert spans[1].get_tag(test.STATUS) == test.Status.PASS.value
        assert spans[1].get_tag(test.RESULT) == test.Status.XFAIL.value
        assert spans[1].get_tag(XFAIL_REASON) == "test should xfail"

    def test_xfail_runxfail_fails(self):
        """Test xfail with --runxfail flags should not crash when failing."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.xfail(reason='should fail')
            def test_should_fail():
                assert 0

        """
        )
        file_name = os.path.basename(py_file.strpath)
        self.inline_run("--ddtrace", "--runxfail", file_name)
        spans = self.pop_spans()

        assert len(spans) == 1
        assert spans[0].get_tag(test.STATUS) == test.Status.FAIL.value

    def test_xfail_runxfail_passes(self):
        """Test xfail with --runxfail flags should not crash when passing."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.xfail(reason='should fail')
            def test_should_pass():
                assert 1

        """
        )
        file_name = os.path.basename(py_file.strpath)
        self.inline_run("--ddtrace", "--runxfail", file_name)
        spans = self.pop_spans()

        assert len(spans) == 1
        assert spans[0].get_tag(test.STATUS) == test.Status.PASS.value

    def test_xpass_not_strict(self):
        """Test xpass (unexpected passing) with strict=False, should be marked as pass."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.xfail(reason="test should fail")
            def test_should_fail_but_passes():
                pass

            @pytest.mark.xfail(condition=True, reason="test should not xfail")
            def test_should_not_fail():
                pass
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=2)
        spans = self.pop_spans()

        assert len(spans) == 2
        assert spans[0].get_tag(test.STATUS) == test.Status.PASS.value
        assert spans[0].get_tag(test.RESULT) == test.Status.XPASS.value
        assert spans[0].get_tag(XFAIL_REASON) == "test should fail"
        assert spans[1].get_tag(test.STATUS) == test.Status.PASS.value
        assert spans[1].get_tag(test.RESULT) == test.Status.XPASS.value
        assert spans[1].get_tag(XFAIL_REASON) == "test should not xfail"

    def test_xpass_strict(self):
        """Test xpass (unexpected passing) with strict=True, should be marked as fail."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.xfail(reason="test should fail", strict=True)
            def test_should_fail():
                pass
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(failed=1)
        spans = self.pop_spans()

        assert len(spans) == 1
        assert spans[0].get_tag(test.STATUS) == test.Status.FAIL.value
        assert spans[0].get_tag(test.RESULT) == test.Status.XPASS.value
        # Note: XFail (strict=True) does not mark the reason with result.wasxfail but into result.longrepr,
        # however it provides the entire traceback/error into longrepr.
        assert "test should fail" in spans[0].get_tag(XFAIL_REASON)

    def test_tags(self):
        """Test ddspan tags."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.dd_tags(mark="dd_tags")
            def test_fixture(ddspan):
                assert ddspan is not None
                ddspan.set_tag("world", "hello")
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 1
        assert spans[0].get_tag("world") == "hello"
        assert spans[0].get_tag("mark") == "dd_tags"
        assert spans[0].get_tag(test.STATUS) == test.Status.PASS.value

    def test_service_name_repository_name(self):
        """Test span's service name is set to repository name."""
        self.monkeypatch.setenv("APPVEYOR", "true")
        self.monkeypatch.setenv("APPVEYOR_REPO_PROVIDER", "github")
        self.monkeypatch.setenv("APPVEYOR_REPO_NAME", "test-repository-name")
        py_file = self.testdir.makepyfile(
            """
            import os

            def test_service(ddspan):
                assert 'test-repository-name' == ddspan.service
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.subprocess_run("--ddtrace", file_name)
        rec.assert_outcomes(passed=1)

    def test_default_service_name(self):
        """Test default service name if no repository name found."""
        providers = [provider for (provider, extract) in ci.PROVIDERS]
        for provider in providers:
            self.monkeypatch.delenv(provider, raising=False)
        py_file = self.testdir.makepyfile(
            """
            def test_service(ddspan):
                assert ddspan.service == "pytest"
                assert ddspan.name == "pytest.test"
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.subprocess_run("--ddtrace", file_name)
        rec.assert_outcomes(passed=1)

    def test_dd_service_name(self):
        """Test dd service name."""
        self.monkeypatch.setenv("DD_SERVICE", "mysvc")
        if "DD_PYTEST_SERVICE" in os.environ:
            self.monkeypatch.delenv("DD_PYTEST_SERVICE")

        py_file = self.testdir.makepyfile(
            """
            import os

            def test_service(ddspan):
                assert 'mysvc' == os.getenv('DD_SERVICE')
                assert os.getenv('DD_PYTEST_SERVICE') is None
                assert 'mysvc' == ddspan.service
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.subprocess_run("--ddtrace", file_name)
        assert 0 == rec.ret

    def test_dd_pytest_service_name(self):
        """Test integration service name."""
        self.monkeypatch.setenv("DD_SERVICE", "mysvc")
        self.monkeypatch.setenv("DD_PYTEST_SERVICE", "pymysvc")
        self.monkeypatch.setenv("DD_PYTEST_OPERATION_NAME", "mytest")

        py_file = self.testdir.makepyfile(
            """
            import os

            def test_service(ddspan):
                assert 'mysvc' == os.getenv('DD_SERVICE')
                assert 'pymysvc' == os.getenv('DD_PYTEST_SERVICE')
                assert 'pymysvc' == ddspan.service
                assert 'mytest' == ddspan.name
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.subprocess_run("--ddtrace", file_name)
        assert 0 == rec.ret

    def test_dd_origin_tag_propagated_to_every_span(self):
        """Test that every span in generated trace has the dd_origin tag."""
        py_file = self.testdir.makepyfile(
            """
            import pytest
            import ddtrace
            from ddtrace import Pin

            def test_service(ddspan, pytestconfig):
                tracer = Pin.get_from(pytestconfig).tracer
                with tracer.trace("SPAN2") as span2:
                    with tracer.trace("SPAN3") as span3:
                        with tracer.trace("SPAN4") as span4:
                            assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)

        spans = self.pop_spans()
        # Check if spans tagged with dd_origin after encoding and decoding as the tagging occurs at encode time
        encoder = self.tracer.encoder
        encoder.put(spans)
        trace = encoder.encode()
        (decoded_trace,) = self.tracer.encoder._decode(trace)
        assert len(decoded_trace) == 4
        for span in decoded_trace:
            assert span[b"meta"][b"_dd.origin"] == b"ciapp-test"

    def test_pytest_doctest_module(self):
        """Test that pytest with doctest works as expected."""
        py_file = self.testdir.makepyfile(
            """
        '''
        This module supplies one function foo(). For example,
        >>> foo()
        42
        '''

        def foo():
            '''Returns the answer to life, the universe, and everything.
            >>> foo()
            42
            '''
            return 42

        def test_foo():
            assert foo() == 42
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", "--doctest-modules", file_name)
        rec.assertoutcome(passed=3)
        spans = self.pop_spans()

        assert len(spans) == 3
        for span in spans:
            assert span.get_tag(test.SUITE) == file_name.partition(".py")[0]

    def test_pytest_sets_sample_priority(self):
        """Test sample priority tags."""
        py_file = self.testdir.makepyfile(
            """
            def test_sample_priority():
                assert True is True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 1
        assert spans[0].get_metric(SAMPLING_PRIORITY_KEY) == 1

    def test_pytest_exception(self):
        """Test that pytest sets exception information correctly."""
        py_file = self.testdir.makepyfile(
            """
        def test_will_fail():
            assert 2 == 1
        """
        )
        file_name = os.path.basename(py_file.strpath)
        self.inline_run("--ddtrace", file_name)
        spans = self.pop_spans()

        assert len(spans) == 1
        test_span = spans[0]
        assert test_span.get_tag(test.STATUS) == test.Status.FAIL.value
        assert test_span.get_tag("error.type").endswith("AssertionError") is True
        assert test_span.get_tag("error.msg") == "assert 2 == 1"
        assert test_span.get_tag("error.stack") is not None

    def test_pytest_tests_with_internal_exceptions_get_test_status(self):
        """Test that pytest sets a fail test status if it has an internal exception."""
        py_file = self.testdir.makepyfile(
            """
        import pytest

        # This is bad usage and results in a pytest internal exception
        @pytest.mark.filterwarnings("ignore::pytest.ExceptionThatDoesNotExist")
        def test_will_fail_internally():
            assert 2 == 2
        """
        )
        file_name = os.path.basename(py_file.strpath)
        self.inline_run("--ddtrace", file_name)
        spans = self.pop_spans()

        assert len(spans) == 1
        test_span = spans[0]
        assert test_span.get_tag(test.STATUS) == test.Status.FAIL.value
        assert test_span.get_tag("error.type") is None

    def test_pytest_broken_setup_will_be_reported_as_error(self):
        """Test that pytest sets a fail test status if the setup fails."""
        py_file = self.testdir.makepyfile(
            """
        import pytest

        @pytest.fixture
        def my_fixture():
            raise Exception('will fail in setup')
            yield

        def test_will_fail_in_setup(my_fixture):
            assert 1 == 1
        """
        )
        file_name = os.path.basename(py_file.strpath)
        self.inline_run("--ddtrace", file_name)
        spans = self.pop_spans()

        assert len(spans) == 1
        test_span = spans[0]

        assert test_span.get_tag(test.STATUS) == test.Status.FAIL.value
        assert test_span.get_tag("error.type").endswith("Exception") is True
        assert test_span.get_tag("error.msg") == "will fail in setup"
        assert test_span.get_tag("error.stack") is not None

    def test_pytest_broken_teardown_will_be_reported_as_error(self):
        """Test that pytest sets a fail test status if the teardown fails."""
        py_file = self.testdir.makepyfile(
            """
        import pytest

        @pytest.fixture
        def my_fixture():
            yield
            raise Exception('will fail in teardown')

        def test_will_fail_in_teardown(my_fixture):
            assert 1 == 1
        """
        )
        file_name = os.path.basename(py_file.strpath)
        self.inline_run("--ddtrace", file_name)
        spans = self.pop_spans()

        assert len(spans) == 1
        test_span = spans[0]

        assert test_span.get_tag(test.STATUS) == test.Status.FAIL.value
        assert test_span.get_tag("error.type").endswith("Exception") is True
        assert test_span.get_tag("error.msg") == "will fail in teardown"
        assert test_span.get_tag("error.stack") is not None

    def test_pytest_will_report_its_version(self):
        py_file = self.testdir.makepyfile(
            """
        import pytest

        def test_will_work():
            assert 1 == 1
        """
        )
        file_name = os.path.basename(py_file.strpath)
        self.inline_run("--ddtrace", file_name)
        spans = self.pop_spans()

        assert len(spans) == 1
        test_span = spans[0]

        assert test_span.get_tag(test.FRAMEWORK_VERSION) == pytest.__version__

    def test_pytest_will_report_codeowners(self):
        file_names = []
        py_team_a_file = self.testdir.makepyfile(
            test_team_a="""
        import pytest

        def test_team_a():
            assert 1 == 1
        """
        )
        file_names.append(os.path.basename(py_team_a_file.strpath))
        py_team_b_file = self.testdir.makepyfile(
            test_team_b="""
        import pytest

        def test_team_b():
            assert 1 == 1
        """
        )
        file_names.append(os.path.basename(py_team_b_file.strpath))
        codeowners = "* @default-team\n{0} @team-b @backup-b".format(os.path.basename(py_team_b_file.strpath))
        self.testdir.makefile("", CODEOWNERS=codeowners)

        self.inline_run("--ddtrace", *file_names)
        spans = self.pop_spans()

        assert len(spans) == 2
        assert json.loads(spans[0].get_tag(test.CODEOWNERS)) == ["@default-team"], spans[0]
        assert json.loads(spans[1].get_tag(test.CODEOWNERS)) == ["@team-b", "@backup-b"], spans[1]


@pytest.mark.parametrize(
    "repository_url,repository_name",
    [
        ("https://github.com/DataDog/dd-trace-py.git", "dd-trace-py"),
        ("https://github.com/DataDog/dd-trace-py", "dd-trace-py"),
        ("git@github.com:DataDog/dd-trace-py.git", "dd-trace-py"),
        ("git@github.com:DataDog/dd-trace-py", "dd-trace-py"),
        ("dd-trace-py", "dd-trace-py"),
        ("git@hostname.com:org/repo-name.git", "repo-name"),
        ("git@hostname.com:org/repo-name", "repo-name"),
        ("ssh://git@hostname.com:org/repo-name", "repo-name"),
        ("git+git://github.com/org/repo-name.git", "repo-name"),
        ("git+ssh://github.com/org/repo-name.git", "repo-name"),
        ("git+https://github.com/org/repo-name.git", "repo-name"),
    ],
)
def test_repository_name_extracted(repository_url, repository_name):
    assert _extract_repository_name(repository_url) == repository_name


def test_repository_name_not_extracted_warning():
    """If provided an invalid repository url, should raise warning and return original repository url"""
    repository_url = "https://github.com:organ[ization/repository-name"
    with mock.patch("ddtrace.contrib.pytest.plugin.log") as mock_log:
        extracted_repository_name = _extract_repository_name(repository_url)
        assert extracted_repository_name == repository_url
    mock_log.warning.assert_called_once_with("Repository name cannot be parsed from repository_url: %s", repository_url)
