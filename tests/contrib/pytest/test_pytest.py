import json
import os
import textwrap
import typing as t
from unittest import mock

import pytest

import ddtrace
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.contrib.internal.pytest.constants import XFAIL_REASON
from ddtrace.contrib.internal.pytest.patch import get_version
from ddtrace.contrib.internal.pytest.plugin import is_enabled
from ddtrace.ext import ci
from ddtrace.ext import git
from ddtrace.ext import test
from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility._api_client import ITRData
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility.constants import ITR_CORRELATION_ID_TAG_NAME
from ddtrace.internal.ci_visibility.encoder import CIVisibilityEncoderV01
from tests.ci_visibility.api_client._util import _make_fqdn_suite_ids
from tests.ci_visibility.api_client._util import _make_fqdn_test_ids
from tests.ci_visibility.util import _ci_override_env
from tests.ci_visibility.util import _get_default_ci_env_vars
from tests.ci_visibility.util import _get_default_civisibility_ddconfig
from tests.ci_visibility.util import _patch_dummy_writer
from tests.contrib.patch import emit_integration_and_version_to_test_agent
from tests.utils import TracerTestCase
from tests.utils import override_env


def _get_spans_from_list(
    spans: t.List[ddtrace.trace.Span],
    span_type: str,
    name: str = None,
    status: t.Optional[str] = None,
) -> t.List[ddtrace.trace.Span]:
    _names_map = {
        "session": ("test_session_end",),
        "module": ("test_module_end", "test.module"),
        "suite": ("test_suite_end", "test.suite"),
        "test": ("test", "test.name"),
    }

    if span_type == "session" and name is not None:
        raise ValueError("Cannot get session spans with a name")

    target_type = _names_map[span_type][0]
    target_name = _names_map[span_type][1] if name is not None else None

    selected_spans = []

    for span in spans:
        # filter out spans that don't match our desired criteria
        if span.get_tag("type") != target_type:
            continue

        if name is not None and span.get_tag(target_name) != name:
            continue

        if status is not None and span.get_tag("test.status") != status:
            continue

        selected_spans.append(span)

    return selected_spans


def _fetch_test_to_skip_side_effect(itr_data):
    def _():
        CIVisibility._instance._itr_data = itr_data

    return _


class PytestTestCaseBase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    @pytest.fixture(autouse=True)
    def _dummy_check_enabled_features(self):
        """By default, assume that _check_enabled_features() returns an ITR-disabled response.

        Tests that need a different response should re-patch the CIVisibility object.
        """
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            yield

    def inline_run(self, *args, mock_ci_env=True, block_gitlab_env=False, project_dir=None, extra_env=None):
        """Execute test script with test tracer."""

        class CIVisibilityPlugin:
            @staticmethod
            def pytest_configure(config):
                if is_enabled(config):
                    with _patch_dummy_writer():
                        assert CIVisibility.enabled
                        CIVisibility.disable()
                        CIVisibility.enable(tracer=self.tracer, config=ddtrace.config.pytest)
                        CIVisibility._instance._itr_meta[ITR_CORRELATION_ID_TAG_NAME] = "pytestitrcorrelationid"

            @staticmethod
            def pytest_unconfigure(config):
                if CIVisibility.enabled:
                    CIVisibility.disable()

        if project_dir is None:
            project_dir = str(self.testdir.tmpdir)

        _test_env = _get_default_ci_env_vars(dict(DD_API_KEY="foobar.baz"), mock_ci_env=mock_ci_env)
        if mock_ci_env:
            _test_env["CI_PROJECT_DIR"] = project_dir

        if block_gitlab_env:
            _test_env["GITLAB_CI"] = "0"

        if extra_env:
            _test_env.update(extra_env)

        with _ci_override_env(_test_env, replace_os_env=True):
            return self.testdir.inline_run("-p", "no:randomly", *args, plugins=[CIVisibilityPlugin()])

    def subprocess_run(self, *args, env: t.Optional[t.Dict[str, str]] = None):
        """Execute test script with test tracer."""
        _base_env = dict(DD_API_KEY="foobar.baz")
        if env is not None:
            _base_env.update(env)
        with _ci_override_env(_base_env):
            return self.testdir.runpytest_subprocess(*args)


class PytestTestCase(PytestTestCaseBase):
    def test_and_emit_get_version(self):
        version = get_version()
        assert isinstance(version, str)
        assert version != ""

        emit_integration_and_version_to_test_agent("pytest", version)

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

        assert len(spans) == 4

    def test_pytest_command(self):
        """Test that the pytest run command is stored on a test span."""
        py_file = self.testdir.makepyfile(
            """
            def test_ok():
                assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()
        test_span = spans[0]
        assert test_span.get_tag("test.command") == "pytest -p no:randomly --ddtrace {}".format(file_name)

    def test_pytest_command_test_session_name(self):
        """Test that the pytest run command is used to set the test session name."""
        py_file = self.testdir.makepyfile(
            """
            def test_ok():
                assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.set_test_session_name"
        ) as set_test_session_name_mock:
            self.inline_run("--ddtrace", file_name)

        set_test_session_name_mock.assert_called_once_with(
            test_command="pytest -p no:randomly --ddtrace {}".format(file_name)
        )

    def test_ini_no_ddtrace(self):
        """Test ini config, overridden by --no-ddtrace cli parameter."""
        self.testdir.makefile(".ini", pytest="[pytest]\nddtrace=1\n")
        py_file = self.testdir.makepyfile(
            """
            def test_ok():
                assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--no-ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 0

    def test_pytest_command_no_ddtrace(self):
        """Test that --no-ddtrace has precedence over --ddtrace."""
        py_file = self.testdir.makepyfile(
            """
            def test_ok():
                assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", "--no-ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()

        assert len(spans) == 0

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
        assert len(spans) == 8
        test_spans = [span for span in spans if span.get_tag("type") == "test"]
        for i in range(len(expected_params)):
            assert json.loads(test_spans[i].get_tag(test.PARAMETERS)) == {
                "arguments": {"item": str(expected_params[i])},
                "metadata": {},
            }

    def test_parameterize_case_complex_objects(self):
        """Test parametrize case with complex objects."""
        py_file = self.testdir.makepyfile(
            """
            from unittest.mock import MagicMock
            import pytest

            class A:
                def __init__(self, name, value):
                    self.name = name
                    self.value = value

                def __repr__(self):
                    return f"{self.__class__.__name__}(name={self.name}, value={self.value})"

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
            "A(name=test_name",
            "A(name=test_name, value=A(name=inner_name, value=value))",
            "<function item_param>",
            "{'a': A(name=test_name, value=value), 'b': [1, 2, 3]}",
            "<MagicMock id=",
            "Could not encode",
            "{('x', 'y'): 12345}",
        ]
        assert len(spans) == 10
        test_spans = [span for span in spans if span.get_tag("type") == "test"]
        for i in range(len(expected_params_contains)):
            assert expected_params_contains[i] in test_spans[i].get_tag(test.PARAMETERS)

    def test_parameterize_case_encoding_error(self):
        """Test parametrize case with complex objects that cannot be JSON encoded."""
        py_file = self.testdir.makepyfile(
            """
            from unittest.mock import MagicMock
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

        assert len(spans) == 4
        test_span = _get_spans_from_list(spans, "test")[0]
        assert json.loads(test_span.get_tag(test.PARAMETERS)) == {
            "arguments": {"item": "Could not encode"},
            "metadata": {},
        }

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

        assert len(spans) == 5
        test_spans = [span for span in spans if span.get_tag("type") == "test"]
        assert test_spans[0].get_tag(test.STATUS) == test.Status.SKIP.value
        assert test_spans[0].get_tag(test.SKIP_REASON) == "reason"
        assert test_spans[1].get_tag(test.STATUS) == test.Status.SKIP.value
        assert test_spans[1].get_tag(test.SKIP_REASON) == "reason"
        assert test_spans[0].get_tag("component") == "pytest"
        assert test_spans[1].get_tag("component") == "pytest"

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

        assert len(spans) == 5
        test_spans = [span for span in spans if span.get_tag("type") == "test"]
        assert test_spans[0].get_tag(test.STATUS) == test.Status.SKIP.value
        assert test_spans[0].get_tag(test.SKIP_REASON) == "reason"
        assert test_spans[1].get_tag(test.STATUS) == test.Status.SKIP.value
        assert test_spans[1].get_tag(test.SKIP_REASON) == "reason"
        assert test_spans[0].get_tag("component") == "pytest"
        assert test_spans[1].get_tag("component") == "pytest"

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

        assert len(spans) == 5
        test_spans = [span for span in spans if span.get_tag("type") == "test"]
        assert test_spans[0].get_tag(test.STATUS) == test.Status.PASS.value
        assert test_spans[0].get_tag(test.RESULT) == test.Status.XFAIL.value
        assert test_spans[0].get_tag(XFAIL_REASON) == "test should fail"
        assert test_spans[1].get_tag(test.STATUS) == test.Status.PASS.value
        assert test_spans[1].get_tag(test.RESULT) == test.Status.XFAIL.value
        assert test_spans[1].get_tag(XFAIL_REASON) == "test should xfail"
        assert test_spans[0].get_tag("component") == "pytest"
        assert test_spans[1].get_tag("component") == "pytest"

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

        assert len(spans) == 4
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

        assert len(spans) == 4
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

        assert len(spans) == 5
        test_should_fail_span = _get_spans_from_list(spans, "test", "test_should_fail_but_passes")[0]
        assert test_should_fail_span.get_tag(test.STATUS) == test.Status.PASS.value
        assert test_should_fail_span.get_tag(test.RESULT) == test.Status.XPASS.value
        assert test_should_fail_span.get_tag(XFAIL_REASON) == "test should fail"
        assert test_should_fail_span.get_tag("component") == "pytest"

        test_should_not_fail_span = _get_spans_from_list(spans, "test", "test_should_not_fail")[0]
        assert test_should_not_fail_span.get_tag(test.STATUS) == test.Status.PASS.value
        assert test_should_not_fail_span.get_tag(test.RESULT) == test.Status.XPASS.value
        assert test_should_not_fail_span.get_tag(XFAIL_REASON) == "test should not xfail"
        assert test_should_not_fail_span.get_tag("component") == "pytest"

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

        assert len(spans) == 4
        span = _get_spans_from_list(spans, "test")[0]
        assert span.get_tag(test.STATUS) == test.Status.FAIL.value
        assert span.get_tag(test.RESULT) == test.Status.XPASS.value
        # Note: XFail (strict=True) does not mark the reason with result.wasxfail but into result.longrepr,
        # however it provides the entire traceback/error into longrepr.
        assert "test should fail" in span.get_tag(XFAIL_REASON)

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

        assert len(spans) == 4
        test_span = spans[0]
        assert test_span.get_tag("world") == "hello"
        assert test_span.get_tag("mark") == "dd_tags"
        assert test_span.get_tag(test.STATUS) == test.Status.PASS.value
        assert test_span.get_tag("component") == "pytest"

    def test_service_name_repository_name(self):
        """Test span's service name is set to repository name."""
        py_file = self.testdir.makepyfile(
            """
            import os

            def test_service(ddspan):
                assert 'test-repository-name' == ddspan.service
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.subprocess_run(
            "--ddtrace",
            file_name,
            env={
                "APPVEYOR": "true",
                "APPVEYOR_REPO_PROVIDER": "github",
                "APPVEYOR_REPO_NAME": "test-repository-name",
            },
        )
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

        rec = self.subprocess_run("--ddtrace", file_name, env={"DD_SERVICE": "mysvc"})
        assert 0 == rec.ret

    def test_dd_pytest_service_name(self):
        """Test integration service name."""
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
        rec = self.subprocess_run(
            "--ddtrace",
            file_name,
            env={"DD_SERVICE": "mysvc", "DD_PYTEST_SERVICE": "pymysvc", "DD_PYTEST_OPERATION_NAME": "mytest"},
        )
        assert 0 == rec.ret

    def test_dd_origin_tag_propagated_to_every_span(self):
        """Test that every span in generated trace has the dd_origin tag."""
        py_file = self.testdir.makepyfile(
            """
            import pytest
            import ddtrace
            from ddtrace.trace import Pin

            def test_service(ddtracer):
                with ddtracer.trace("SPAN2") as span2:
                    with ddtracer.trace("SPAN3") as span3:
                        with ddtracer.trace("SPAN4") as span4:
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
        trace, _ = encoder.encode()
        (decoded_trace,) = self.tracer.encoder._decode(trace)
        assert len(decoded_trace) == 7
        for span in decoded_trace:
            assert span[b"meta"][b"_dd.origin"] == b"ciapp-test"

        ci_agentless_encoder = CIVisibilityEncoderV01(0, 0)
        ci_agentless_encoder.put(spans)
        event_payload, _ = ci_agentless_encoder.encode()
        decoded_event_payload = self.tracer.encoder._decode(event_payload)
        assert len(decoded_event_payload[b"events"]) == 7
        for event in decoded_event_payload[b"events"]:
            assert event[b"content"][b"meta"][b"_dd.origin"] == b"ciapp-test"
        pass

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

        assert len(spans) == 6
        non_session_spans = [span for span in spans if span.get_tag("type") != "test_session_end"]
        for span in non_session_spans:
            if span.get_tag("type") == "test_suite_end":
                assert span.get_tag(test.SUITE) == file_name
        test_session_span = _get_spans_from_list(spans, "session")[0]
        assert test_session_span.get_tag("test.command") == (
            "pytest -p no:randomly --ddtrace --doctest-modules " "test_pytest_doctest_module.py"
        )

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

        assert len(spans) == 4
        assert spans[0].get_metric(_SAMPLING_PRIORITY_KEY) == 1

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

        assert len(spans) == 4
        test_span = spans[0]
        assert test_span.get_tag(test.STATUS) == test.Status.FAIL.value
        assert test_span.get_tag("error.type").endswith("AssertionError") is True
        assert test_span.get_tag(ERROR_MSG) == "assert 2 == 1"
        assert test_span.get_tag("error.stack") is not None
        assert test_span.get_tag("component") == "pytest"

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

        assert len(spans) == 4
        test_span = spans[0]
        assert test_span.get_tag(test.STATUS) == test.Status.FAIL.value
        assert test_span.get_tag("error.type") is None
        assert test_span.get_tag("component") == "pytest"

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

        assert len(spans) == 4
        test_span = _get_spans_from_list(spans, "test")[0]

        assert test_span.get_tag(test.STATUS) == test.Status.FAIL.value
        assert test_span.get_tag("error.type").endswith("Exception") is True
        assert test_span.get_tag(ERROR_MSG) == "will fail in setup"
        assert test_span.get_tag("error.stack") is not None
        assert test_span.get_tag("component") == "pytest"

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

        assert len(spans) == 4
        test_span = _get_spans_from_list(spans, "test")[0]

        assert test_span.get_tag(test.STATUS) == test.Status.FAIL.value
        assert test_span.get_tag("error.type").endswith("Exception") is True
        assert test_span.get_tag(ERROR_MSG) == "will fail in teardown"
        assert test_span.get_tag("error.stack") is not None
        assert test_span.get_tag("component") == "pytest"

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

        assert len(spans) == 4
        test_span = _get_spans_from_list(spans, "test")[0]

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
        codeowners = "* @default-team\n{0} @team-b @backup-b\n".format(os.path.basename(py_team_b_file.strpath))
        self.testdir.makefile("", CODEOWNERS=codeowners)

        self.inline_run("--ddtrace", *file_names)
        spans = self.pop_spans()
        assert len(spans) == 6
        test_spans = [span for span in spans if span.get_tag("type") == "test"]
        assert json.loads(test_spans[0].get_tag(test.CODEOWNERS)) == ["@default-team"], test_spans[0]
        assert json.loads(test_spans[1].get_tag(test.CODEOWNERS)) == ["@team-b", "@backup-b"], test_spans[1]

    def test_pytest_session(self):
        """Test that running pytest will generate a test session span."""
        self.inline_run("--ddtrace")
        spans = self.pop_spans()
        assert len(spans) == 1
        assert spans[0].get_tag("type") == "test_session_end"
        assert spans[0].get_tag("test_session_id") == str(spans[0].span_id)
        assert spans[0].get_tag("test.command") == "pytest -p no:randomly --ddtrace"

    def test_pytest_suite(self):
        """Test that running pytest on a test file will generate a test suite span."""
        py_file = self.testdir.makepyfile(
            """
            def test_ok():
                assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1)
        spans = self.pop_spans()
        test_suite_span = spans[3]
        test_module_span = spans[2]
        test_session_span = spans[1]
        assert test_suite_span.get_tag("type") == "test_suite_end"
        assert test_suite_span.get_tag("test_session_id") == str(test_session_span.span_id)
        assert test_suite_span.get_tag("test_module_id") == str(test_module_span.span_id)
        assert test_suite_span.get_tag("test_suite_id") == str(test_suite_span.span_id)
        assert test_suite_span.get_tag("test.status") == "pass"
        assert test_module_span.get_tag("test.status") == "pass"
        assert test_module_span.get_tag("test.module") == ""
        assert test_module_span.get_tag("test.status") == "pass"
        assert test_session_span.get_tag("test.status") == "pass"
        assert test_suite_span.get_tag("test.command") == "pytest -p no:randomly --ddtrace {}".format(file_name)
        assert test_suite_span.get_tag("test.suite") == str(file_name)

    def test_pytest_suites(self):
        """
        Test that running pytest on two files with 1 test each will generate
         1 test session span, 2 test suite spans, and 2 test spans.
        """
        file_names = []
        file_a = self.testdir.makepyfile(
            test_a="""
        def test_ok():
            assert True
        """
        )
        file_names.append(os.path.basename(file_a.strpath))
        file_b = self.testdir.makepyfile(
            test_b="""
        def test_not_ok():
            assert 0
        """
        )
        file_names.append(os.path.basename(file_b.strpath))
        self.inline_run("--ddtrace")
        spans = self.pop_spans()

        assert len(spans) == 6
        test_session_span = _get_spans_from_list(spans, "session")[0]
        assert test_session_span.name == "pytest.test_session"
        assert test_session_span.parent_id is None
        test_spans = _get_spans_from_list(spans, "test")
        test_module_spans = _get_spans_from_list(spans, "module")
        test_module_span_ids = [span.span_id for span in test_module_spans]
        for test_span in test_spans:
            assert test_span.name == "pytest.test"
            assert test_span.parent_id is None
        test_suite_spans = _get_spans_from_list(spans, "suite")
        for test_suite_span in test_suite_spans:
            assert test_suite_span.name == "pytest.test_suite"
            assert test_suite_span.parent_id in test_module_span_ids
        for test_module_span in test_module_spans:
            assert test_module_span.name == "pytest.test_module"
            assert test_module_span.parent_id == test_session_span.span_id

    def test_pytest_test_class_does_not_prematurely_end_test_suite(self):
        """Test that given a test class, the test suite span will not end prematurely."""
        self.testdir.makepyfile(
            test_a="""
                def test_outside_class_before():
                    assert True
                class TestClass:
                    def test_ok(self):
                        assert True
                def test_outside_class_after():
                    assert True
                """
        )
        rec = self.inline_run("--ddtrace")
        rec.assertoutcome(passed=3)
        spans = self.pop_spans()
        assert len(spans) == 6
        test_span_a_inside_class = spans[1]
        test_span_a_outside_after_class = spans[2]
        test_suite_a_span = spans[5]
        assert test_suite_a_span.get_tag("type") == "test_suite_end"
        assert test_suite_a_span.start_ns + test_suite_a_span.duration_ns >= test_span_a_outside_after_class.start_ns

    def test_pytest_suites_one_fails_propagates(self):
        """Test that if any tests fail, the status propagates upwards."""
        file_names = []
        file_a = self.testdir.makepyfile(
            test_a="""
                def test_ok():
                    assert True
                """
        )
        file_names.append(os.path.basename(file_a.strpath))
        file_b = self.testdir.makepyfile(
            test_b="""
                def test_not_ok():
                    assert 0
                """
        )
        file_names.append(os.path.basename(file_b.strpath))
        self.inline_run("--ddtrace")
        spans = self.pop_spans()
        test_session_span = _get_spans_from_list(spans, "session")[0]
        test_module_spans = _get_spans_from_list(spans, "module")
        test_a_module_span = test_module_spans[0]
        assert test_a_module_span.get_tag("type") == "test_module_end"
        test_a_suite_span = _get_spans_from_list(spans, "suite", "test_a.py")[0]
        assert test_a_suite_span.get_tag("type") == "test_suite_end"
        test_b_module_span = test_module_spans[0]
        assert test_b_module_span.get_tag("type") == "test_module_end"
        test_b_suite_span = _get_spans_from_list(spans, "suite", "test_b.py")[0]
        assert test_b_suite_span.get_tag("type") == "test_suite_end"
        assert test_session_span.get_tag("test.status") == "fail"
        assert test_a_suite_span.get_tag("test.status") == "pass"
        assert test_b_suite_span.get_tag("test.status") == "fail"
        assert test_a_module_span.get_tag("test.status") == "fail"
        assert test_b_module_span.get_tag("test.status") == "fail"

    def test_pytest_suites_one_skip_does_not_propagate(self):
        """Test that if not all tests skip, the status does not propagate upwards."""
        file_names = []
        file_a = self.testdir.makepyfile(
            test_a="""
                def test_ok():
                    assert True
                """
        )
        file_names.append(os.path.basename(file_a.strpath))
        file_b = self.testdir.makepyfile(
            test_b="""
                import pytest
                @pytest.mark.skip(reason="Because")
                def test_not_ok():
                    assert 0
                """
        )
        file_names.append(os.path.basename(file_b.strpath))
        self.inline_run("--ddtrace")
        spans = self.pop_spans()
        test_session_span = _get_spans_from_list(spans, "session")[0]
        test_module_spans = _get_spans_from_list(spans, "module")
        test_a_module_span = test_module_spans[0]
        assert test_a_module_span.get_tag("type") == "test_module_end"
        test_a_suite_span = _get_spans_from_list(spans, "suite", "test_a.py")[0]
        assert test_a_suite_span.get_tag("type") == "test_suite_end"
        test_b_module_span = test_module_spans[0]
        assert test_b_module_span.get_tag("type") == "test_module_end"
        test_b_suite_span = _get_spans_from_list(spans, "suite", "test_b.py")[0]
        assert test_b_suite_span.get_tag("type") == "test_suite_end"
        assert test_session_span.get_tag("test.status") == "pass"
        assert test_a_suite_span.get_tag("test.status") == "pass"
        assert test_b_suite_span.get_tag("test.status") == "skip"
        assert test_a_module_span.get_tag("test.status") == "pass"
        assert test_b_module_span.get_tag("test.status") == "pass"

    def test_pytest_all_tests_pass_status_propagates(self):
        """Test that if all tests pass, the status propagates upwards."""
        py_file = self.testdir.makepyfile(
            """
            def test_ok():
                assert True

            def test_ok_2():
                assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=2)
        spans = self.pop_spans()
        for span in spans:
            assert span.get_tag("test.status") == "pass"

    def test_pytest_status_fail_propagates(self):
        """Test that if any tests fail, that status propagates upwards.
        In other words, any test failure will cause the test suite to be marked as fail, as well as module, and session.
        """
        py_file = self.testdir.makepyfile(
            """
            def test_ok():
                assert True

            def test_not_ok():
                assert 0
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(passed=1, failed=1)
        spans = self.pop_spans()
        test_span_ok = spans[0]
        test_span_not_ok = spans[1]
        test_suite_span = spans[4]
        test_session_span = spans[2]
        test_module_span = spans[3]
        assert test_suite_span.get_tag("type") == "test_suite_end"
        assert test_module_span.get_tag("type") == "test_module_end"
        assert test_session_span.get_tag("type") == "test_session_end"
        assert test_span_ok.get_tag("test.status") == "pass"
        assert test_span_not_ok.get_tag("test.status") == "fail"
        assert test_suite_span.get_tag("test.status") == "fail"
        assert test_module_span.get_tag("test.status") == "fail"
        assert test_session_span.get_tag("test.status") == "fail"

    def test_pytest_all_tests_skipped_propagates(self):
        """Test that if all tests are skipped, that status propagates upwards.
        In other words, all test skips will cause the test suite to be marked as skipped,
        and the same logic for module and session.
        """
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.skip(reason="Because")
            def test_not_ok_but_skipped():
                assert 0

            @pytest.mark.skip(reason="Because")
            def test_also_not_ok_but_skipped():
                assert 0
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(skipped=2)
        spans = self.pop_spans()
        for span in spans:
            assert span.get_tag("test.status") == "skip"

    def test_pytest_not_all_tests_skipped_does_not_propagate(self):
        """Test that if not all tests are skipped, that status does not propagate upwards."""
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.skip(reason="Because")
            def test_not_ok_but_skipped():
                assert 0

            def test_ok():
                assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(skipped=1, passed=1)
        spans = self.pop_spans()
        test_session_span = _get_spans_from_list(spans, "session")[0]
        test_module_span = _get_spans_from_list(spans, "module")[0]
        test_suite_span = _get_spans_from_list(spans, "suite")[0]
        test_span_skipped = _get_spans_from_list(spans, "test", "test_not_ok_but_skipped")[0]
        test_span_ok = _get_spans_from_list(spans, "test", "test_ok")[0]
        assert test_suite_span.get_tag("type") == "test_suite_end"
        assert test_module_span.get_tag("type") == "test_module_end"
        assert test_session_span.get_tag("type") == "test_session_end"
        assert test_span_skipped.get_tag("test.status") == "skip"
        assert test_span_ok.get_tag("test.status") == "pass"
        assert test_suite_span.get_tag("test.status") == "pass"
        assert test_module_span.get_tag("test.status") == "pass"
        assert test_session_span.get_tag("test.status") == "pass"

    def test_pytest_some_skipped_tests_does_not_propagate_in_testcase(self):
        """Test that if not all tests are skipped, that status does not propagate upwards."""
        py_file = self.testdir.makepyfile(
            """
            import unittest
            import pytest

            class MyTest(unittest.TestCase):

                @pytest.mark.skip(reason="Because")
                def test_not_ok_but_skipped(self):
                    assert 0

                def test_ok(self):
                    assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(skipped=1, passed=1)
        spans = self.pop_spans()
        test_span_skipped = spans[0]
        test_span_ok = spans[1]
        test_suite_span = spans[4]
        test_session_span = spans[2]
        test_module_span = spans[3]
        assert test_suite_span.get_tag("type") == "test_suite_end"
        assert test_module_span.get_tag("type") == "test_module_end"
        assert test_session_span.get_tag("type") == "test_session_end"
        assert test_span_skipped.get_tag("test.status") == "skip"
        assert test_span_ok.get_tag("test.status") == "pass"
        assert test_suite_span.get_tag("test.status") == "pass"
        assert test_session_span.get_tag("test.status") == "pass"
        assert test_module_span.get_tag("test.status") == "pass"

    def test_pytest_all_skipped_tests_does_propagate_in_testcase(self):
        """Test that if all tests are skipped, that status is propagated upwards."""
        py_file = self.testdir.makepyfile(
            """
            import unittest
            import pytest

            class MyTest(unittest.TestCase):

                @pytest.mark.skip(reason="Because")
                def test_not_ok_but_skipped(self):
                    assert 0

                @pytest.mark.skip(reason="Because")
                def test_ok_but_skipped(self):
                    assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(skipped=2, passed=0)
        spans = self.pop_spans()
        test_session_span = [s for s in spans if s.get_tag("type") == "test_session_end"][0]
        test_suite_span = [s for s in spans if s.get_tag("type") == "test_suite_end"][0]
        test_module_span = [s for s in spans if s.get_tag("type") == "test_module_end"][0]
        test_span_ok = [s for s in spans if "test_ok_but_skipped" in str(s.get_tag("test.name"))][0]
        test_span_skipped = [s for s in spans if "test_not_ok_but_skipped" in str(s.get_tag("test.name"))][0]
        assert test_suite_span.get_tag("type") == "test_suite_end"
        assert test_module_span.get_tag("type") == "test_module_end"
        assert test_session_span.get_tag("type") == "test_session_end"
        assert test_span_skipped.get_tag("test.status") == "skip"
        assert test_span_ok.get_tag("test.status") == "skip"
        assert test_suite_span.get_tag("test.status") == "skip"
        assert test_session_span.get_tag("test.status") == "skip"
        assert test_module_span.get_tag("test.status") == "skip"

    def test_pytest_failed_tests_propagate_in_testcase(self):
        """Test that if any test fails, that status is propagated upwards."""
        py_file = self.testdir.makepyfile(
            """
            import unittest
            import pytest

            class MyTest(unittest.TestCase):

                def test_not_ok(self):
                    assert 0

                def test_ok(self):
                    assert True
        """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run("--ddtrace", file_name)
        rec.assertoutcome(failed=1, passed=1)
        spans = self.pop_spans()
        test_span_skipped = spans[0]
        test_span_ok = spans[1]
        test_suite_span = spans[4]
        test_session_span = spans[2]
        test_module_span = spans[3]
        assert test_suite_span.get_tag("type") == "test_suite_end"
        assert test_module_span.get_tag("type") == "test_module_end"
        assert test_session_span.get_tag("type") == "test_session_end"
        assert test_span_skipped.get_tag("test.status") == "fail"
        assert test_span_ok.get_tag("test.status") == "pass"
        assert test_suite_span.get_tag("test.status") == "fail"
        assert test_session_span.get_tag("test.status") == "fail"
        assert test_module_span.get_tag("test.status") == "fail"

    def test_pytest_module(self):
        """Test that running pytest on a test package will generate a test module span."""
        package_a_dir = self.testdir.mkpydir("test_package_a")
        os.chdir(str(package_a_dir))
        with open("test_a.py", "w+") as fd:
            fd.write(
                """def test_ok():
                assert True"""
            )
        self.testdir.chdir()
        self.inline_run("--ddtrace")
        spans = self.pop_spans()
        for span in spans:
            assert span.get_tag("test.status") == "pass"
        assert len(spans) == 4
        test_module_span = _get_spans_from_list(spans, "module")[0]
        test_session_span = _get_spans_from_list(spans, "session")[0]
        assert test_module_span.get_tag("type") == "test_module_end"
        assert test_module_span.get_tag("test_session_id") == str(test_session_span.span_id)
        assert test_module_span.get_tag("test_module_id") == str(test_module_span.span_id)
        assert test_module_span.get_tag("test.command") == "pytest -p no:randomly --ddtrace"
        assert test_module_span.get_tag("test.module") == "test_package_a"
        assert test_module_span.get_tag("test.module_path") == "test_package_a"

    def test_pytest_modules(self):
        """
        Test that running pytest on two packages with 1 test each will generate
         1 test session span, 2 test module spans, 2 test suite spans, and 2 test spans.
        """
        package_a_dir = self.testdir.mkpydir("test_package_a")
        os.chdir(str(package_a_dir))
        with open("test_a.py", "w+") as fd:
            fd.write(
                """def test_ok():
                assert True"""
            )
        package_b_dir = self.testdir.mkpydir("test_package_b")
        os.chdir(str(package_b_dir))
        with open("test_b.py", "w+") as fd:
            fd.write(
                """def test_not_ok():
                assert 0"""
            )
        self.testdir.chdir()
        self.inline_run("--ddtrace")
        spans = self.pop_spans()

        assert len(spans) == 7
        test_session_span = _get_spans_from_list(spans, "session")[0]
        assert test_session_span.name == "pytest.test_session"
        assert test_session_span.get_tag("test.status") == "fail"
        test_module_spans = _get_spans_from_list(spans, "module")
        for span in test_module_spans:
            assert span.name == "pytest.test_module"
            assert span.parent_id == test_session_span.span_id
        test_suite_spans = _get_spans_from_list(spans, "suite")
        for i in range(len(test_suite_spans)):
            assert test_suite_spans[i].name == "pytest.test_suite"
            assert test_suite_spans[i].parent_id == test_module_spans[i].span_id
        test_spans = _get_spans_from_list(spans, "test")
        for i in range(len(test_spans)):
            assert test_spans[i].name == "pytest.test"
            assert test_spans[i].parent_id is None
            assert test_spans[i].get_tag("test_module_id") == str(test_module_spans[i].span_id)

    def test_pytest_test_class_does_not_prematurely_end_test_module(self):
        """Test that given a test class, the test module span will not end prematurely."""
        package_a_dir = self.testdir.mkpydir("test_package_a")
        os.chdir(str(package_a_dir))
        with open("test_a.py", "w+") as fd:
            fd.write(
                "def test_ok():\n\tassert True\n"
                "class TestClassOuter:\n"
                "\tclass TestClassInner:\n"
                "\t\tdef test_class_inner(self):\n\t\t\tassert True\n"
                "\tdef test_class_outer(self):\n\t\tassert True\n"
                "def test_after_class():\n\tassert True"
            )
        self.testdir.chdir()
        rec = self.inline_run("--ddtrace")
        rec.assertoutcome(passed=4)
        spans = self.pop_spans()
        assert len(spans) == 7
        test_span_outside_after_class = spans[3]
        test_module_span = spans[5]
        assert test_module_span.start_ns + test_module_span.duration_ns >= test_span_outside_after_class.start_ns

    def test_pytest_packages_skip_one(self):
        """
        Test that running pytest on two packages with 1 test each, but skipping one package will generate
         1 test session span, 2 test module spans, 2 test suite spans, and 2 test spans.
        """
        package_a_dir = self.testdir.mkpydir("test_package_a")
        os.chdir(str(package_a_dir))
        with open("test_a.py", "w+") as fd:
            fd.write(
                """def test_not_ok():
                assert 0"""
            )
        package_b_dir = self.testdir.mkpydir("test_package_b")
        os.chdir(str(package_b_dir))
        with open("test_b.py", "w+") as fd:
            fd.write(
                """def test_ok():
                assert True"""
            )
        self.testdir.chdir()
        self.inline_run("--ignore=test_package_a", "--ddtrace")
        spans = self.pop_spans()
        assert len(spans) == 4
        test_session_span = spans[1]
        assert test_session_span.name == "pytest.test_session"
        assert test_session_span.get_tag("test.status") == "pass"
        test_module_span = spans[2]
        assert test_module_span.name == "pytest.test_module"
        assert test_module_span.parent_id == test_session_span.span_id
        assert test_module_span.get_tag("test.status") == "pass"
        test_suite_span = spans[3]
        assert test_suite_span.name == "pytest.test_suite"
        assert test_suite_span.parent_id == test_module_span.span_id
        assert test_suite_span.get_tag("test_module_id") == str(test_module_span.span_id)
        assert test_suite_span.get_tag("test.status") == "pass"
        test_span = spans[0]
        assert test_span.name == "pytest.test"
        assert test_span.parent_id is None
        assert test_span.get_tag("test_module_id") == str(test_module_span.span_id)
        assert test_span.get_tag("test.status") == "pass"

    def test_pytest_module_path(self):
        """
        Test that running pytest on two nested packages with 1 test each will generate
         1 test session span, 2 test module spans, 2 test suite spans, and 2 test spans,
         with the module spans including correct module paths.
        """
        package_outer_dir = self.testdir.mkpydir("test_outer_package")
        os.chdir(str(package_outer_dir))
        with open("test_outer_abc.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    """
                import pytest

                @pytest.mark.parametrize("paramslash", ["c/d", "/d/c", "f/"])
                def test_ok_1(paramslash):
                    assert True"""
                )
            )
        os.mkdir("test_inner_package")
        os.chdir("test_inner_package")
        with open("__init__.py", "w+"):
            pass
        with open("test_inner_abc.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    """
                import pytest

                @pytest.mark.parametrize("slashparam", ["a/b", "/b/a", "a/"])
                def test_ok_2(slashparam):
                    assert True

                """
                )
            )
        self.testdir.chdir()
        self.inline_run("--ddtrace")
        spans = self.pop_spans()

        assert len(spans) == 11
        test_module_spans = sorted(
            [span for span in spans if span.get_tag("type") == "test_module_end"],
            key=lambda s: s.get_tag("test.module"),
        )
        assert test_module_spans[0].get_tag("test.module") == "test_outer_package"
        assert test_module_spans[0].get_tag("test.module_path") == "test_outer_package"
        assert test_module_spans[1].get_tag("test.module") == "test_outer_package.test_inner_package"
        assert test_module_spans[1].get_tag("test.module_path") == "test_outer_package/test_inner_package"
        test_suite_spans = sorted(
            [span for span in spans if span.get_tag("type") == "test_suite_end"], key=lambda s: s.get_tag("test.suite")
        )
        assert test_suite_spans[0].get_tag("test.suite") == "test_inner_abc.py"
        assert test_suite_spans[1].get_tag("test.suite") == "test_outer_abc.py"

        test_spans = _get_spans_from_list(spans, "test")
        for test_span in test_spans:
            if test_span.get_tag("test.name").startswith("test_ok_1"):
                assert test_span.get_tag("test.module_path") == "test_outer_package"
            elif test_span.get_tag("test.name").startswith("test_ok_2"):
                assert test_span.get_tag("test.module_path") == "test_outer_package/test_inner_package"
            else:
                raise ValueError("Unexpected span name")

    def test_pytest_module_path_empty(self):
        """
        Test that running pytest without module will create an empty module span with empty path.
        """
        self.testdir.makepyfile(
            lib_fn="""
        def lib_fn():
            return True
        """
        )
        # py_cov_file =
        self.testdir.makepyfile(
            test_cov="""
        import pytest
        from lib_fn import lib_fn

        def test_cov():
            assert lib_fn()
        """
        )
        self.testdir.chdir()
        self.inline_run("--ddtrace")
        spans = self.pop_spans()

        assert len(spans) == 4
        test_module_spans = _get_spans_from_list(spans, "module")
        assert len(test_module_spans) == 1
        assert test_module_spans[0].get_tag("test.module") == ""
        assert test_module_spans[0].get_tag("test.module_path") == ""
        test_suite_spans = [span for span in spans if span.get_tag("type") == "test_suite_end"]
        assert len(test_suite_spans) == 1
        assert test_suite_spans[0].get_tag("test.suite") == "test_cov.py"

    def test_pytest_will_report_git_metadata(self):
        py_file = self.testdir.makepyfile(
            """
        import pytest

        def test_will_work():
            assert 1 == 1
        """
        )
        file_name = os.path.basename(py_file.strpath)
        with mock.patch("ddtrace.internal.ci_visibility.recorder._get_git_repo") as ggr:
            ggr.return_value = self.git_repo
            self.inline_run("--ddtrace", file_name, mock_ci_env=False)
            spans = self.pop_spans()

        assert len(spans) == 4
        test_span = spans[0]

        assert test_span.get_tag(git.COMMIT_MESSAGE) == "this is a commit msg"
        assert test_span.get_tag(git.COMMIT_AUTHOR_DATE) == "2021-01-19T09:24:53-0400"
        assert test_span.get_tag(git.COMMIT_AUTHOR_NAME) == "John Doe"
        assert test_span.get_tag(git.COMMIT_AUTHOR_EMAIL) == "john@doe.com"
        assert test_span.get_tag(git.COMMIT_COMMITTER_DATE) == "2021-01-20T04:37:21-0400"
        assert test_span.get_tag(git.COMMIT_COMMITTER_NAME) == "Jane Doe"
        assert test_span.get_tag(git.COMMIT_COMMITTER_EMAIL) == "jane@doe.com"
        assert test_span.get_tag(git.BRANCH)
        assert test_span.get_tag(git.COMMIT_SHA)
        assert test_span.get_tag(git.REPOSITORY_URL)

    def test_pytest_skip_suite_by_path(self):
        """
        Test that running pytest on two nested packages with 2 tests each (making suore that both function-based and
        class-based tests result in the suites properly being marked as skipped).

        It should generate 1 test session span (passed), 2 test module spans (passed), 4 test suite spans (2 passed,
        2 skipped), and 4 test spans (2 passed, 2 skipped).

        The expected result looks like:
        - session: passed
          - module "test_outer_package": passed
            - suite "test_outer_abc.py": skipped
              - test "test_outer_ok": skipped
            - suite "test_outer_class_abc.py": passed
              - test "test_outer_class_ok": passed
          - module "test_outer_package.test_inner_package": passed
            - suite "test_inner_abc.py": passed
              - test "test_inner_ok": passed
            - suite "test_inner_class_abc.py": skipped
              - test "test_inner_class_ok": skipped
        """
        package_outer_dir = self.testdir.mkpydir("test_outer_package")
        os.chdir(str(package_outer_dir))
        with open("test_outer_abc.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    """
            def test_outer_ok():
                assert True
            """
                )
            )
        with open("test_outer_class_abc.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    """
            class TestOuterClass:
                def test_outer_class_ok(self):
                    assert True
            """
                )
            )
        os.mkdir("test_inner_package")
        os.chdir("test_inner_package")
        with open("__init__.py", "w+"):
            pass
        with open("test_inner_abc.py", "w+") as fd:
            fd.write(
                """def test_inner_ok():
                assert True"""
            )
        with open("test_inner_class_abc.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    """
            class TestInnerClass:
                def test_inner_class_ok(self):
                    assert True
            """
                )
            )
        self.testdir.chdir()

        _itr_data = ITRData(
            skippable_items=_make_fqdn_suite_ids(
                [
                    ("test_outer_package.test_inner_package", "test_inner_abc.py"),
                    ("test_outer_package", "test_outer_abc.py"),
                ]
            )
        )

        with override_env(dict(_DD_CIVISIBILITY_ITR_SUITE_MODE="True")), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.test_skipping_enabled",
            return_value=True,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.is_itr_enabled",
            return_value=True,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip",
            side_effect=_fetch_test_to_skip_side_effect(_itr_data),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig",
            _get_default_civisibility_ddconfig(ITR_SKIPPING_LEVEL.SUITE),
        ):
            self.inline_run("--ddtrace")

        spans = self.pop_spans()
        assert len(spans) == 11

        session_span = [span for span in spans if span.get_tag("type") == "test_session_end"][0]
        assert session_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert session_span.get_tag("test.itr.tests_skipping.tests_skipped") == "true"
        assert session_span.get_tag("_dd.ci.itr.tests_skipped") == "true"
        assert session_span.get_tag("test.itr.tests_skipping.type") == "suite"
        assert session_span.get_metric("test.itr.tests_skipping.count") == 2

        module_spans = [span for span in spans if span.get_tag("type") == "test_module_end"]
        assert len(module_spans) == 2
        outer_module_span = [span for span in module_spans if span.get_tag("test.module") == "test_outer_package"][0]
        assert outer_module_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert outer_module_span.get_tag("test.itr.tests_skipping.tests_skipped") == "true"
        assert outer_module_span.get_tag("_dd.ci.itr.tests_skipped") == "true"
        assert outer_module_span.get_tag("test.itr.tests_skipping.type") == "suite"
        assert outer_module_span.get_metric("test.itr.tests_skipping.count") == 1
        inner_module_span = [
            span for span in module_spans if span.get_tag("test.module") == "test_outer_package.test_inner_package"
        ][0]
        assert inner_module_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert inner_module_span.get_tag("test.itr.tests_skipping.tests_skipped") == "true"
        assert inner_module_span.get_tag("_dd.ci.itr.tests_skipped") == "true"
        assert inner_module_span.get_tag("test.itr.tests_skipping.type") == "suite"
        assert inner_module_span.get_metric("test.itr.tests_skipping.count") == 1

        passed_spans = [x for x in spans if x.get_tag("test.status") == "pass"]
        assert len(passed_spans) == 7
        skipped_spans = [x for x in spans if x.get_tag("test.status") == "skip"]
        assert len(skipped_spans) == 4

        skipped_suite_spans = [x for x in skipped_spans if x.get_tag("type") == "test_suite_end"]
        assert len(skipped_suite_spans) == 2
        for skipped_suite_span in skipped_suite_spans:
            assert skipped_suite_span.get_tag("test.skipped_by_itr") == "true"
            assert skipped_suite_span.get_tag("itr_correlation_id") == "pytestitrcorrelationid"

        skipped_test_spans = [x for x in skipped_spans if x.get_tag("type") == "test"]
        assert len(skipped_test_spans) == 2
        for skipped_test_span in skipped_test_spans:
            assert skipped_test_span.get_tag("test.skipped_by_itr") == "true"
            assert skipped_test_span.get_tag("itr_correlation_id") is None

    def test_pytest_skip_all_test_suites(self):
        """
        Test that running pytest on two nested packages with 1 test each. It should generate
        1 test session span, 2 test module spans, 2 test suite spans, and 2 test spans, but
        all test suites are skipped with ITR. All the spans and tags are reported accordingly.
        """
        package_outer_dir = self.testdir.mkpydir("test_outer_package")
        os.chdir(str(package_outer_dir))
        with open("test_outer_abc.py", "w+") as fd:
            fd.write(
                """def test_outer_ok():
                assert True"""
            )
        os.mkdir("test_inner_package")
        os.chdir("test_inner_package")
        with open("__init__.py", "w+"):
            pass
        with open("test_inner_abc.py", "w+") as fd:
            fd.write(
                """def test_inner_ok():
                assert True"""
            )
        self.testdir.chdir()
        with override_env(dict(_DD_CIVISIBILITY_ITR_SUITE_MODE="True")), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.test_skipping_enabled",
            return_value=True,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.is_itr_enabled",
            return_value=True,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip"
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._should_skip_path", return_value=True
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.is_item_itr_skippable", return_value=True
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig",
            _get_default_civisibility_ddconfig(ITR_SKIPPING_LEVEL.SUITE),
        ):
            self.inline_run("--ddtrace")

        spans = self.pop_spans()
        assert len(spans) == 7

        session_span = _get_spans_from_list(spans, "session")[0]
        assert session_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert session_span.get_tag("test.itr.tests_skipping.tests_skipped") == "true"
        assert session_span.get_tag("_dd.ci.itr.tests_skipped") == "true"
        assert session_span.get_tag("test.itr.tests_skipping.type") == "suite"
        assert session_span.get_metric("test.itr.tests_skipping.count") == 2

        module_spans = _get_spans_from_list(spans, "module")
        for module_span in module_spans:
            assert module_span.get_metric("test.itr.tests_skipping.count") == 1
            assert module_span.get_tag("test.itr.tests_skipping.type") == "suite"
            assert module_span.get_tag("test.itr.tests_skipping.tests_skipped") == "true"
            assert module_span.get_tag("test.itr.tests_skipping.enabled") == "true"

        passed_spans = [x for x in spans if x.get_tag("test.status") == "pass"]
        assert len(passed_spans) == 0
        skipped_spans = [x for x in spans if x.get_tag("test.status") == "skip"]
        assert len(skipped_spans) == 7

        skipped_suite_spans = [
            x for x in spans if x.get_tag("test.status") == "skip" and x.get_tag("type") == "test_suite_end"
        ]
        assert len(skipped_suite_spans) == 2
        for skipped_suite_span in skipped_suite_spans:
            assert skipped_suite_span.get_tag("test.skipped_by_itr") == "true"

        skipped_test_spans = [x for x in spans if x.get_tag("test.status") == "skip" and x.get_tag("type") == "test"]
        assert len(skipped_test_spans) == 2
        for skipped_test_span in skipped_test_spans:
            assert skipped_test_span.get_tag("test.skipped_by_itr") == "true"

    def test_pytest_skip_none_test_suites(self):
        """
        Test that running pytest on two nested packages with 1 test each. It should generate
        1 test session span, 2 test module spans, 2 test suite spans, and 2 test spans, and
        no test suites are skipped with ITR. All the spans and tags are reported accordingly.
        """
        package_outer_dir = self.testdir.mkpydir("test_outer_package")
        os.chdir(str(package_outer_dir))
        with open("test_outer_abc.py", "w+") as fd:
            fd.write(
                """def test_outer_ok():
                assert True"""
            )
        os.mkdir("test_inner_package")
        os.chdir("test_inner_package")
        with open("__init__.py", "w+"):
            pass
        with open("test_inner_abc.py", "w+") as fd:
            fd.write(
                """def test_inner_ok():
                assert True"""
            )
        self.testdir.chdir()
        with override_env(dict(_DD_CIVISIBILITY_ITR_SUITE_MODE="True")), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.test_skipping_enabled",
            return_value=True,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.is_itr_enabled",
            return_value=True,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip"
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._should_skip_path", return_value=False
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig",
            _get_default_civisibility_ddconfig(ITR_SKIPPING_LEVEL.SUITE),
        ):
            self.inline_run("--ddtrace")

        spans = self.pop_spans()
        assert len(spans) == 7

        session_span = [span for span in spans if span.get_tag("type") == "test_session_end"][0]
        assert session_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert session_span.get_tag("test.itr.tests_skipping.tests_skipped") == "false"
        assert session_span.get_tag("_dd.ci.itr.tests_skipped") == "false"
        assert session_span.get_tag("test.itr.tests_skipping.type") == "suite"
        assert session_span.get_metric("test.itr.tests_skipping.count") == 0

        module_spans = [span for span in spans if span.get_tag("type") == "test_module_end"]
        for module_span in module_spans:
            assert module_span.get_metric("test.itr.tests_skipping.count") == 0
            assert module_span.get_tag("test.itr.tests_skipping.type") == "suite"
            assert module_span.get_tag("test.itr.tests_skipping.tests_skipped") == "false"
            assert module_span.get_tag("test.itr.tests_skipping.enabled") == "true"

        passed_spans = [x for x in spans if x.get_tag("test.status") == "pass"]
        assert len(passed_spans) == 7
        skipped_spans = [x for x in spans if x.get_tag("test.status") == "skip"]
        assert len(skipped_spans) == 0

    def test_pytest_skip_suite_by_path_but_test_skipping_not_enabled(self):
        """
        Test that running pytest on two nested packages with 1 test each. It should generate
        1 test session span, 2 test module spans, 2 test suite spans, and 2 test spans,
        both suites are to be skipped with ITR, but test skipping is disabled.
        """
        package_outer_dir = self.testdir.mkpydir("test_outer_package")
        os.chdir(str(package_outer_dir))
        with open("test_outer_abc.py", "w+") as fd:
            fd.write(
                """def test_outer_ok():
                assert True"""
            )
        os.mkdir("test_inner_package")
        os.chdir("test_inner_package")
        with open("__init__.py", "w+"):
            pass
        with open("test_inner_abc.py", "w+") as fd:
            fd.write(
                """def test_inner_ok():
                assert True"""
            )
        self.testdir.chdir()

        _itr_data = ITRData(
            skippable_items=_make_fqdn_suite_ids(
                [
                    ("test_outer_package.test_inner_package", "test_inner_abc.py"),
                    ("test_outer_package", "test_outer_abc.py"),
                ]
            )
        )

        with override_env(dict(_DD_CIVISIBILITY_ITR_SUITE_MODE="True")), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.is_itr_enabled",
            return_value=True,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip",
            side_effect=_fetch_test_to_skip_side_effect(_itr_data),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig",
            _get_default_civisibility_ddconfig(ITR_SKIPPING_LEVEL.SUITE),
        ):
            self.inline_run("--ddtrace")

        spans = self.pop_spans()
        assert len(spans) == 7

        session_span = [span for span in spans if span.get_tag("type") == "test_session_end"][0]
        assert session_span.get_tag("test.itr.tests_skipping.enabled") == "false"

        module_spans = [span for span in spans if span.get_tag("type") == "test_module_end"]
        assert len(module_spans) == 2
        for module_span in module_spans:
            assert module_span.get_tag("test.itr.tests_skipping.enabled") == "false"

        test_suite_spans = [span for span in spans if span.get_tag("type") == "test_suite_end"]
        assert len(test_suite_spans) == 2

        test_spans = [span for span in spans if span.get_tag("type") == "test"]
        assert len(test_spans) == 2
        passed_test_spans = [x for x in spans if x.get_tag("type") == "test" and x.get_tag("test.status") == "pass"]
        assert len(passed_test_spans) == 2

    def test_pytest_skip_tests_by_path_but_test_skipping_not_enabled(self):
        """
        Test that running pytest on two nested packages with 1 test each. It should generate
        1 test session span, 2 test module spans, 2 test suite spans, and 2 test spans,
        both suites are to be skipped with ITR, but test skipping is disabled.
        """
        package_outer_dir = self.testdir.mkpydir("test_outer_package")
        os.chdir(str(package_outer_dir))
        with open("test_outer_abc.py", "w+") as fd:
            fd.write(
                """def test_outer_ok():
                assert True"""
            )
        os.mkdir("test_inner_package")
        os.chdir("test_inner_package")
        with open("__init__.py", "w+"):
            pass
        with open("test_inner_abc.py", "w+") as fd:
            fd.write(
                """def test_inner_ok():
                assert True"""
            )
        self.testdir.chdir()

        _itr_data = ITRData(
            skippable_items=_make_fqdn_test_ids(
                [
                    ("test_outer_package.test_inner_package", "test_inner_abc.py", "test_inner_ok"),
                    ("test_outer_package.test_inner_package", "test_inner_abc.py", "test_inner_shouldskip_skipif"),
                    ("test_outer_package", "test_outer_abc.py", "test_outer_ok"),
                ]
            )
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip",
            side_effect=_fetch_test_to_skip_side_effect(_itr_data),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.is_itr_enabled",
            return_value=True,
        ):
            self.inline_run("--ddtrace")

        spans = self.pop_spans()
        assert len(spans) == 7

        session_span = [span for span in spans if span.get_tag("type") == "test_session_end"][0]
        assert session_span.get_tag("test.itr.tests_skipping.enabled") == "false"

        module_spans = [span for span in spans if span.get_tag("type") == "test_module_end"]
        assert len(module_spans) == 2
        for module_span in module_spans:
            assert module_span.get_tag("test.itr.tests_skipping.enabled") == "false"

        test_suite_spans = [span for span in spans if span.get_tag("type") == "test_suite_end"]
        assert len(test_suite_spans) == 2

        test_spans = [span for span in spans if span.get_tag("type") == "test"]
        assert len(test_spans) == 2
        passed_test_spans = [x for x in spans if x.get_tag("type") == "test" and x.get_tag("test.status") == "pass"]
        assert len(passed_test_spans) == 2

    def test_pytest_unskippable_tests_forced_run_in_suite_level(self):
        package_outer_dir = self.testdir.mkpydir("test_outer_package")
        os.chdir(str(package_outer_dir))
        with open("test_outer_abc.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    def test_outer_ok():
                        assert True
                    """
                    )
                )
            )
        os.mkdir("test_inner_package")
        os.chdir("test_inner_package")
        with open("__init__.py", "w+"):
            pass
        with open("test_inner_abc.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    """
                import pytest
                def test_inner_ok():
                    assert True

                @pytest.mark.skipif(False, reason="datadog_itr_unskippable")
                def test_inner_unskippable():
                    assert True

                @pytest.mark.skipif(False, reason="datadog_itr_unskippable")
                @pytest.mark.skipif(True, reason="skipped anyway")
                def test_inner_shouldskip_skipif():
                    assert False

                @pytest.mark.skipif(False, reason="datadog_itr_unskippable")
                @pytest.mark.skip(reason="just skip it")
                def test_inner_shouldskip_skip():
                    assert True

                @pytest.mark.skipif(False, reason="datadog_itr_unskippable")
                def test_inner_itr_wont_skip():
                    assert True

                @pytest.mark.skipif(False, reason="datadog_itr_unskippable")
                @pytest.mark.skipif(False, reason="was not going to skip anyway")
                def test_inner_wasnot_going_to_skip_skipif():
                    assert True
                """
                )
            )

        self.testdir.chdir()

        _itr_data = ITRData(
            skippable_items=_make_fqdn_suite_ids(
                [
                    ("test_outer_package.test_inner_package", "test_inner_abc.py"),
                    ("test_outer_package", "test_outer_abc.py"),
                ]
            )
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip",
            side_effect=_fetch_test_to_skip_side_effect(_itr_data),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.test_skipping_enabled",
            return_value=True,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.is_itr_enabled",
            return_value=True,
        ), override_env(
            {"_DD_CIVISIBILITY_ITR_SUITE_MODE": "True"}
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig",
            _get_default_civisibility_ddconfig(ITR_SKIPPING_LEVEL.SUITE),
        ):
            self.inline_run("--ddtrace")

        spans = self.pop_spans()
        assert len(spans) == 12

        session_span = _get_spans_from_list(spans, "session")[0]
        assert session_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert session_span.get_tag("test.itr.tests_skipping.tests_skipped") == "true"
        assert session_span.get_tag("_dd.ci.itr.tests_skipped") == "true"
        assert session_span.get_tag("test.itr.unskippable") == "true"
        assert session_span.get_tag("test.itr.forced_run") == "true"

        module_spans = _get_spans_from_list(spans, "module")
        assert len(module_spans) == 2

        outer_module_span = _get_spans_from_list(module_spans, "module", "test_outer_package")[0]
        assert outer_module_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert outer_module_span.get_tag("test.itr.tests_skipping.tests_skipped") == "true"
        assert outer_module_span.get_tag("_dd.ci.itr.tests_skipped") == "true"
        assert outer_module_span.get_tag("test.itr.forced_run") == "false"

        inner_module_span = _get_spans_from_list(module_spans, "module", "test_outer_package.test_inner_package")[0]
        assert inner_module_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert inner_module_span.get_tag("test.itr.tests_skipping.tests_skipped") == "false"
        assert inner_module_span.get_tag("_dd.ci.itr.tests_skipped") == "false"
        assert inner_module_span.get_tag("test.itr.forced_run") == "true"

        test_suite_spans = _get_spans_from_list(spans, "suite")
        assert len(test_suite_spans) == 2

        inner_suite_span = _get_spans_from_list(test_suite_spans, "suite", "test_inner_abc.py")[0]
        assert inner_suite_span.get_tag("test.itr.forced_run") == "true"
        assert inner_suite_span.get_tag("test.itr.unskippable") == "true"

        test_spans = _get_spans_from_list(spans, "test")
        assert len(test_spans) == 7
        passed_test_spans = _get_spans_from_list(test_spans, "test", status="pass")
        assert len(passed_test_spans) == 4
        skipped_test_spans = _get_spans_from_list(test_spans, "test", status="skip")
        assert len(skipped_test_spans) == 3

        test_inner_ok_span = [span for span in spans if span.get_tag("test.name") == "test_inner_ok"][0]
        assert test_inner_ok_span.get_tag("test.itr.forced_run") == "true"

        test_inner_unskippable_span = _get_spans_from_list(spans, "test", "test_inner_unskippable")[0]
        assert test_inner_unskippable_span.get_tag("test.itr.unskippable") == "true"
        assert test_inner_unskippable_span.get_tag("test.itr.forced_run") == "true"

        test_inner_shouldskip_skipif_span = _get_spans_from_list(spans, "test", "test_inner_shouldskip_skipif")[0]
        assert test_inner_shouldskip_skipif_span.get_tag("test.itr.unskippable") == "true"
        assert test_inner_shouldskip_skipif_span.get_tag("test.status") == "skip"

        test_inner_shouldskip_skip_span = _get_spans_from_list(spans, "test", "test_inner_shouldskip_skip")[0]
        assert test_inner_shouldskip_skip_span.get_tag("test.itr.unskippable") == "true"
        assert test_inner_shouldskip_skip_span.get_tag("test.status") == "skip"

        test_inner_wasnot_going_to_skip_skipif_span = _get_spans_from_list(
            spans, "test", "test_inner_wasnot_going_to_skip_skipif"
        )[0]
        assert test_inner_wasnot_going_to_skip_skipif_span.get_tag("test.itr.unskippable") == "true"

    def test_pytest_unskippable_suite_not_skipped_in_suite_level(self):
        package_outer_dir = self.testdir.mkpydir("test_outer_package")
        os.chdir(str(package_outer_dir))
        with open("test_outer_abc.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    def test_outer_ok():
                        assert True
                    """
                    )
                )
            )
        os.mkdir("test_inner_package")
        os.chdir("test_inner_package")
        with open("__init__.py", "w+"):
            pass
        with open("test_inner_abc.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    """
                import pytest
                pytestmark = pytest.mark.skipif(False, reason="datadog_itr_unskippable")
                def test_inner_ok():
                    assert True

                @pytest.mark.skipif(False, reason="datadog_itr_unskippable")
                def test_inner_unskippable():
                    assert True

                @pytest.mark.skipif(False, reason="datadog_itr_unskippable")
                @pytest.mark.skipif(True, reason="skipped anyway")
                def test_inner_shouldskip_skipif():
                    assert False

                @pytest.mark.skipif(False, reason="datadog_itr_unskippable")
                @pytest.mark.skip(reason="just skip it")
                def test_inner_shouldskip_skip():
                    assert True

                @pytest.mark.skipif(False, reason="datadog_itr_unskippable")
                def test_inner_itr_wont_skip():
                    assert True

                @pytest.mark.skipif(False, reason="datadog_itr_unskippable")
                @pytest.mark.skipif(False, reason="was not going to skip anyway")
                def test_inner_wasnot_going_to_skip_skipif():
                    assert True
                """
                )
            )
        self.testdir.chdir()

        _itr_data = ITRData(
            skippable_items=_make_fqdn_suite_ids(
                [
                    ("test_outer_package.test_inner_package", "test_inner_abc.py"),
                    ("test_outer_package", "test_outer_abc.py"),
                ]
            )
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip",
            side_effect=_fetch_test_to_skip_side_effect(_itr_data),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.test_skipping_enabled",
            return_value=True,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.is_itr_enabled",
            return_value=True,
        ), override_env(
            {"_DD_CIVISIBILITY_ITR_SUITE_MODE": "True"}
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig",
            _get_default_civisibility_ddconfig(ITR_SKIPPING_LEVEL.SUITE),
        ):
            self.inline_run("--ddtrace")

        spans = self.pop_spans()
        assert len(spans) == 12

        session_span = [span for span in spans if span.get_tag("type") == "test_session_end"][0]
        assert session_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert session_span.get_tag("test.itr.tests_skipping.tests_skipped") == "true"
        assert session_span.get_tag("_dd.ci.itr.tests_skipped") == "true"
        assert session_span.get_tag("test.itr.unskippable") == "true"
        assert session_span.get_tag("test.itr.forced_run") == "true"

        module_spans = [span for span in spans if span.get_tag("type") == "test_module_end"]
        assert len(module_spans) == 2

        outer_module_span = [span for span in module_spans if span.get_tag("test.module") == "test_outer_package"][0]
        assert outer_module_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert outer_module_span.get_tag("test.itr.tests_skipping.tests_skipped") == "true"
        assert outer_module_span.get_tag("_dd.ci.itr.tests_skipped") == "true"
        assert outer_module_span.get_tag("test.itr.forced_run") == "false"

        inner_module_span = [
            span for span in module_spans if span.get_tag("test.module") == "test_outer_package.test_inner_package"
        ][0]
        assert inner_module_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert inner_module_span.get_tag("test.itr.tests_skipping.tests_skipped") == "false"
        assert inner_module_span.get_tag("_dd.ci.itr.tests_skipped") == "false"
        assert inner_module_span.get_tag("test.itr.forced_run") == "true"

        test_suite_spans = [span for span in spans if span.get_tag("type") == "test_suite_end"]
        assert len(test_suite_spans) == 2

        inner_suite_span = [span for span in test_suite_spans if span.get_tag("test.suite") == "test_inner_abc.py"][0]
        assert inner_suite_span.get_tag("test.itr.forced_run") == "true"
        assert inner_suite_span.get_tag("test.itr.unskippable") == "true"

        test_spans = [span for span in spans if span.get_tag("type") == "test"]
        assert len(test_spans) == 7
        passed_test_spans = [x for x in spans if x.get_tag("type") == "test" and x.get_tag("test.status") == "pass"]
        assert len(passed_test_spans) == 4
        skipped_test_spans = [x for x in spans if x.get_tag("type") == "test" and x.get_tag("test.status") == "skip"]
        assert len(skipped_test_spans) == 3

        test_inner_unskippable_span = [span for span in spans if span.get_tag("test.name") == "test_inner_unskippable"][
            0
        ]
        assert test_inner_unskippable_span.get_tag("test.itr.unskippable") == "true"
        assert test_inner_unskippable_span.get_tag("test.itr.forced_run") == "true"

        test_inner_shouldskip_skipif_span = [
            span for span in spans if span.get_tag("test.name") == "test_inner_shouldskip_skipif"
        ][0]
        assert test_inner_shouldskip_skipif_span.get_tag("test.itr.unskippable") == "true"
        assert test_inner_shouldskip_skipif_span.get_tag("test.status") == "skip"

        test_inner_shouldskip_skip_span = [
            span for span in spans if span.get_tag("test.name") == "test_inner_shouldskip_skip"
        ][0]
        assert test_inner_shouldskip_skip_span.get_tag("test.itr.unskippable") == "true"
        assert test_inner_shouldskip_skip_span.get_tag("test.status") == "skip"

        test_inner_wasnot_going_to_skip_skipif_span = [
            span for span in spans if span.get_tag("test.name") == "test_inner_wasnot_going_to_skip_skipif"
        ][0]
        assert test_inner_wasnot_going_to_skip_skipif_span.get_tag("test.itr.unskippable") == "true"

    def test_pytest_unskippable_none_skipped_in_suite_level(self):
        """When no tests are skipped, the test.itr.tests_skipping.tests_skipped tag should be false"""
        package_outer_dir = self.testdir.mkpydir("test_outer_package")
        os.chdir(str(package_outer_dir))
        with open("test_outer_abc.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    def test_outer_ok():
                        assert True
                    """
                    )
                )
            )
        os.mkdir("test_inner_package")
        os.chdir("test_inner_package")
        with open("__init__.py", "w+"):
            pass
        with open("test_inner_abc.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    """
                import pytest
                pytestmark = pytest.mark.skipif(False, reason="datadog_itr_unskippable")
                def test_inner_ok():
                    assert True

                def test_inner_unskippable():
                    assert True

                @pytest.mark.skipif(True, reason="skipped anyway")
                def test_inner_shouldskip_skipif():
                    assert False

                @pytest.mark.skip(reason="just skip it")
                def test_inner_shouldskip_skip():
                    assert True

                def test_inner_itr_wont_skip():
                    assert True

                @pytest.mark.skipif(False, reason="was not going to skip anyway")
                def test_inner_wasnot_going_to_skip_skipif():
                    assert True
                """
                )
            )
        self.testdir.chdir()

        _itr_data = ITRData(
            skippable_items=_make_fqdn_suite_ids(
                [
                    ("test_outer_package.test_inner_package", "test_inner_abc.py"),
                ]
            )
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip",
            side_effect=_fetch_test_to_skip_side_effect(_itr_data),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.test_skipping_enabled",
            return_value=True,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.is_itr_enabled",
            return_value=True,
        ), override_env(
            {"_DD_CIVISIBILITY_ITR_SUITE_MODE": "True"}
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig",
            _get_default_civisibility_ddconfig(ITR_SKIPPING_LEVEL.SUITE),
        ):
            self.inline_run("--ddtrace")

        spans = self.pop_spans()
        assert len(spans) == 12

        session_span = [span for span in spans if span.get_tag("type") == "test_session_end"][0]
        assert session_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert session_span.get_tag("test.itr.tests_skipping.tests_skipped") == "false"
        assert session_span.get_tag("_dd.ci.itr.tests_skipped") == "false"
        assert session_span.get_tag("test.itr.unskippable") == "true"
        assert session_span.get_tag("test.itr.forced_run") == "true"

        module_spans = [span for span in spans if span.get_tag("type") == "test_module_end"]
        assert len(module_spans) == 2

        outer_module_span = [span for span in module_spans if span.get_tag("test.module") == "test_outer_package"][0]
        assert outer_module_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert outer_module_span.get_tag("test.itr.tests_skipping.tests_skipped") == "false"
        assert outer_module_span.get_tag("_dd.ci.itr.tests_skipped") == "false"
        assert outer_module_span.get_tag("test.itr.forced_run") == "false"

        inner_module_span = [
            span for span in module_spans if span.get_tag("test.module") == "test_outer_package.test_inner_package"
        ][0]
        assert inner_module_span.get_tag("test.itr.tests_skipping.enabled") == "true"
        assert inner_module_span.get_tag("test.itr.tests_skipping.tests_skipped") == "false"
        assert inner_module_span.get_tag("_dd.ci.itr.tests_skipped") == "false"
        assert inner_module_span.get_tag("test.itr.forced_run") == "true"

    def test_pytest_ddtrace_test_names(self):
        """Tests that the default naming behavior for pytest works as expected

        The test structure is:
        test_outermost_tests.py
        test_outer_package/__init__.py
        test_outer_package/test_outer_package_tests.py
        test_outer_package/test_outer_package_module/test_outer_package_module_tests.py
        test_outer_package/test_inner_package/__init__.py
        test_outer_package/test_inner_package/test_inner_package_tests.py
        test_outer_package/test_inner_package/test_inner_package_module/test_inner_package_module_tests.py

        26 total spans
        1 session
        - 5 modules:
          - (empty string)
            - 1 suite: test_outermost_tests.py: 3 tests
          - test_outer_package
            - 1 suite: test_outer_package_tests.py: 3 tests
          - test_outer_package.test_outer_package_module
            - 1 suite: test_outer_package_module_tests.py: 3 tests
          - test_outer_package.test_inner_package
            - 1 suite: test_inner_package_tests.py: 3 tests
          - test_outer_package.test_inner_package.test_inner_package_module
            - 1 suite: test_inner_package_module_tests.py: 3 tests
        """
        self.testdir.chdir()
        with open("test_outermost_tests.py", "w") as test_outermost_tests_fd:
            test_outermost_tests_fd.write(
                textwrap.dedent(
                    (
                        """
                    def test_outermost_test_ok():
                        assert True

                    class TestOuterMostClassOne():
                        def test_outermost_ok(self):
                            assert True

                    class TestOuterMostClassTwo():
                        def test_outermost_ok(self):
                            assert True
                    """
                    )
                )
            )

        _ = self.testdir.mkpydir("test_outer_package")
        with open("test_outer_package/test_outer_package_tests.py", "w") as outer_fd:
            outer_fd.write(
                textwrap.dedent(
                    (
                        """
                    def test_outer_package_ok():
                        assert True

                    class TestOuterPackageClassOne():
                        def test_outer_package_class_one_ok(self):
                            assert True

                    class TestOuterPackageClassTwo():
                        def test_outer_package_class_two_ok(self):
                            assert True
                    """
                    )
                )
            )

        _ = self.testdir.mkdir("test_outer_package/test_outer_package_module")
        with open(
            "test_outer_package/test_outer_package_module/test_outer_package_module_tests.py", "w"
        ) as outer_package_module_tests_fd:
            outer_package_module_tests_fd.write(
                textwrap.dedent(
                    """
                def test_outer_package_module_ok():
                    assert True

                class TestOuterPackageModuleClassOne():
                    def test_outer_package_module_class_one_ok(self):
                        assert True

                class TestOuterPackageModuleClassTwo():
                    def test_outer_package_module_class_two_ok(self):
                        assert True
                """
                )
            )

        _ = self.testdir.mkpydir("test_outer_package/test_inner_package")
        with open("test_outer_package/test_inner_package/test_inner_package_tests.py", "w") as inner_package_tests_fd:
            inner_package_tests_fd.write(
                textwrap.dedent(
                    """
                def test_inner_package_ok():
                    assert True

                class TestInnerPackageClassOne():
                    def test_inner_package_class_one_ok(self):
                        assert True

                class TestInnerPackageClassTwo():
                    def test_inner_package_class_two_ok(self):
                        assert True
                """
                )
            )

        _ = self.testdir.mkdir("test_outer_package/test_inner_package/test_inner_package_module")
        with open(
            "test_outer_package/test_inner_package/test_inner_package_module/test_inner_package_module_tests.py", "w"
        ) as inner_package_module_tests_fd:
            inner_package_module_tests_fd.write(
                textwrap.dedent(
                    """
                def test_inner_package_module_test_ok():
                    assert True

                class TestInnerPackageModuleClassOne():
                    def test_inner_package_module_class_one_ok(self):
                        assert True

                class TestInnerPackageModuleClassTwo():
                    def test_inner_package_module_class_two_ok(self):
                        assert True
                """
                )
            )

        self.testdir.chdir()
        self.inline_run("--ddtrace")

        spans = self.pop_spans()
        assert len(spans) == 26

        session_span = [span for span in spans if span.get_tag("type") == "test_session_end"][0]
        assert session_span.get_tag("test.status") == "pass"

        sorted_module_names = sorted(
            [span.get_tag("test.module") for span in spans if span.get_tag("type") == "test_module_end"]
        )
        assert len(sorted_module_names) == 5
        assert sorted_module_names == [
            "",
            "test_outer_package",
            "test_outer_package.test_inner_package",
            "test_outer_package.test_inner_package.test_inner_package_module",
            "test_outer_package.test_outer_package_module",
        ]

        sorted_suite_names = sorted(
            [span.get_tag("test.suite") for span in spans if span.get_tag("type") == "test_suite_end"]
        )
        assert len(sorted_suite_names) == 5
        assert sorted_suite_names == [
            "test_inner_package_module_tests.py",
            "test_inner_package_tests.py",
            "test_outer_package_module_tests.py",
            "test_outer_package_tests.py",
            "test_outermost_tests.py",
        ]

        sorted_test_names = sorted([span.get_tag("test.name") for span in spans if span.get_tag("type") == "test"])
        assert len(sorted_test_names) == 15

        assert sorted_test_names == [
            "TestInnerPackageClassOne::test_inner_package_class_one_ok",
            "TestInnerPackageClassTwo::test_inner_package_class_two_ok",
            "TestInnerPackageModuleClassOne::test_inner_package_module_class_one_ok",
            "TestInnerPackageModuleClassTwo::test_inner_package_module_class_two_ok",
            "TestOuterMostClassOne::test_outermost_ok",
            "TestOuterMostClassTwo::test_outermost_ok",
            "TestOuterPackageClassOne::test_outer_package_class_one_ok",
            "TestOuterPackageClassTwo::test_outer_package_class_two_ok",
            "TestOuterPackageModuleClassOne::test_outer_package_module_class_one_ok",
            "TestOuterPackageModuleClassTwo::test_outer_package_module_class_two_ok",
            "test_inner_package_module_test_ok",
            "test_inner_package_ok",
            "test_outer_package_module_ok",
            "test_outer_package_ok",
            "test_outermost_test_ok",
        ]

    def test_pytest_ddtrace_test_names_include_class_opt(self):
        package_outer_dir = self.testdir.mkpydir("test_package")
        os.chdir(str(package_outer_dir))
        with open("test_names.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    def test_ok():
                        assert True

                    class TestClassOne():
                        def test_ok(self):
                            assert True

                    class TestClassTwo():
                        def test_ok(self):
                            assert True
                    """
                    )
                )
            )

        self.testdir.chdir()
        self.inline_run("--ddtrace", "--ddtrace-include-class-name")

        spans = self.pop_spans()
        assert len(spans) == 6

        session_span = [span for span in spans if span.get_tag("type") == "test_session_end"][0]
        assert session_span.get_tag("test.status") == "pass"

        module_span = [span for span in spans if span.get_tag("type") == "test_module_end"][0]
        assert module_span.get_tag("test.module") == "test_package"

        suite_span = [span for span in spans if span.get_tag("type") == "test_suite_end"][0]
        assert suite_span.get_tag("test.module") == "test_package"
        assert suite_span.get_tag("test.suite") == "test_names.py"

        test_spans = [span for span in spans if span.get_tag("type") == "test"]
        assert len(test_spans) == 3
        assert test_spans[0].get_tag("test.module") == "test_package"
        assert test_spans[0].get_tag("test.suite") == "test_names.py"
        assert test_spans[0].get_tag("test.name") == "test_ok"

        assert test_spans[1].get_tag("test.module") == "test_package"
        assert test_spans[1].get_tag("test.suite") == "test_names.py"
        assert test_spans[1].get_tag("test.name") == "TestClassOne::test_ok"

        assert test_spans[2].get_tag("test.module") == "test_package"
        assert test_spans[2].get_tag("test.suite") == "test_names.py"
        assert test_spans[2].get_tag("test.name") == "TestClassTwo::test_ok"

    def test_pytest_ddtrace_name_hooks(self):
        """This only tests that whatever hooks a user defines are being used"""
        with open("conftest.py", "w") as fd:
            fd.write(
                textwrap.dedent(
                    """
                import pytest

                def pytest_ddtrace_get_item_module_name(item):
                    return "module_test." + item.name

                def pytest_ddtrace_get_item_suite_name(item):
                    return "suite_test." + item.name

                def pytest_ddtrace_get_item_test_name(item):
                    return "name_test." + item.name
                """
                )
            )
        package_outer_dir = self.testdir.mkpydir("test_package")
        os.chdir(str(package_outer_dir))
        with open("test_hooks.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    def test_ok():
                        assert True
                    """
                    )
                )
            )

        self.testdir.chdir()
        self.inline_run("--ddtrace")

        spans = self.pop_spans()
        assert len(spans) == 4

        session_span = [span for span in spans if span.get_tag("type") == "test_session_end"][0]
        assert session_span.get_tag("test.status") == "pass"

        module_span = [span for span in spans if span.get_tag("type") == "test_module_end"][0]
        assert module_span.get_tag("test.module") == "module_test.test_ok"

        suite_span = [span for span in spans if span.get_tag("type") == "test_suite_end"][0]
        assert suite_span.get_tag("test.module") == "module_test.test_ok"
        assert suite_span.get_tag("test.suite") == "suite_test.test_ok"

        test_span = [span for span in spans if span.get_tag("type") == "test"][0]
        assert test_span.get_tag("test.module") == "module_test.test_ok"
        assert test_span.get_tag("test.suite") == "suite_test.test_ok"
        assert test_span.get_tag("test.name") == "name_test.test_ok"

    def test_pytest_reports_source_file_data(self):
        package_outer_dir = self.testdir.mkpydir("test_source_package")
        os.chdir(str(package_outer_dir))
        with open("test_names.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    def can_add(x, y):
                        return x + y

                    def is_equal_to_zero(x):
                        return x == 0

                    def test_my_first_test():
                        actual_output = can_add(3, 5)
                        assert actual_output == 8

                    class TestClassOne():
                        def test_my_second_test(self):
                            actual_output = can_add(3, 5)
                            assert not is_equal_to_zero(actual_output)

                    class TestClassTwo():
                        def test_my_third_test(self):
                            assert True
                    """
                    )
                )
            )

        with open("test_string.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    def is_equal_to_hello(x):
                        return x == "hello"

                    def test_my_string_test():
                        actual_output = "hello2"
                        assert not actual_output == is_equal_to_hello(actual_output)
                    """
                    )
                )
            )

        self.testdir.chdir()
        self.inline_run("--ddtrace")

        spans = self.pop_spans()
        assert len(spans) == 8

        test_spans = [span for span in spans if span.get_tag("type") == "test"]
        assert len(test_spans) == 4
        assert test_spans[0].get_tag("test.name") == "test_my_first_test"
        assert test_spans[0].get_tag("test.source.file") == "test_source_package/test_names.py"
        assert test_spans[0].get_metric("test.source.start") == 8
        assert test_spans[0].get_metric("test.source.end") == 11

        assert test_spans[1].get_tag("test.name") == "TestClassOne::test_my_second_test"
        assert test_spans[1].get_tag("test.source.file") == "test_source_package/test_names.py"
        assert test_spans[1].get_metric("test.source.start") == 13
        assert test_spans[1].get_metric("test.source.end") == 16

        assert test_spans[2].get_tag("test.name") == "TestClassTwo::test_my_third_test"
        assert test_spans[2].get_tag("test.source.file") == "test_source_package/test_names.py"
        assert test_spans[2].get_metric("test.source.start") == 18
        assert test_spans[2].get_metric("test.source.end") == 20

        assert test_spans[3].get_tag("test.name") == "test_my_string_test"
        assert test_spans[3].get_tag("test.source.file") == "test_source_package/test_string.py"
        assert test_spans[3].get_metric("test.source.start") == 5
        assert test_spans[3].get_metric("test.source.end") == 8

    def test_pytest_reports_code_coverage_with_cov_flag(self):
        with open("tools.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
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
                    )
                )
            )

        with open("test_tools.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    from tools import add_two_number_list

                    def test_add_two_number_list():
                        a_list = [1,2,3,4,5,6,7,8]
                        b_list = [2,3,4,5,6,7,8,9]
                        actual_output = add_two_number_list(a_list, b_list)

                        assert actual_output == [3,5,7,9,11,13,15,17]
                    """
                    )
                )
            )

        self.testdir.chdir()
        self.inline_run("--ddtrace", "--cov")

        spans = self.pop_spans()
        assert len(spans) == 4
        test_span = spans[0]
        test_session_span = spans[1]
        test_module_span = spans[2]
        test_suite_span = spans[3]

        lines_pct_value = test_session_span.get_metric("test.code_coverage.lines_pct")

        assert lines_pct_value is not None
        assert isinstance(lines_pct_value, float)
        assert test_module_span.get_metric("test.code_coverage.lines_pct") is None
        assert test_suite_span.get_metric("test.code_coverage.lines_pct") is None
        assert test_span.get_metric("test.code_coverage.lines_pct") is None

    def test_pytest_reports_code_coverage_with_cov_flag_specified(self):
        with open("tools.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
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
                    )
                )
            )

        with open("test_tools.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    from tools import add_two_number_list

                    def test_add_two_number_list():
                        a_list = [1,2,3,4,5,6,7,8]
                        b_list = [2,3,4,5,6,7,8,9]
                        actual_output = add_two_number_list(a_list, b_list)

                        assert actual_output == [3,5,7,9,11,13,15,17]
                    """
                    )
                )
            )

        self.testdir.chdir()
        self.inline_run("--ddtrace", "--cov=tools")

        spans = self.pop_spans()
        assert len(spans) == 4
        test_span = spans[0]
        test_session_span = spans[1]
        test_module_span = spans[2]
        test_suite_span = spans[3]

        assert test_session_span.get_metric("test.code_coverage.lines_pct") == 60.0
        assert test_module_span.get_metric("test.code_coverage.lines_pct") is None
        assert test_suite_span.get_metric("test.code_coverage.lines_pct") is None
        assert test_span.get_metric("test.code_coverage.lines_pct") is None

    def test_pytest_does_not_report_code_coverage_with_no_cov_flag_override(self):
        with open("tools.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
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
                    )
                )
            )

        with open("test_tools.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    from tools import add_two_number_list

                    def test_add_two_number_list():
                        a_list = [1,2,3,4,5,6,7,8]
                        b_list = [2,3,4,5,6,7,8,9]
                        actual_output = add_two_number_list(a_list, b_list)

                        assert actual_output == [3,5,7,9,11,13,15,17]
                    """
                    )
                )
            )

        self.testdir.chdir()
        self.inline_run("--ddtrace", "--cov=tools", "--no-cov")

        spans = self.pop_spans()
        assert len(spans) == 4
        test_span = spans[0]
        test_session_span = spans[1]
        test_module_span = spans[2]
        test_suite_span = spans[3]

        assert test_session_span.get_metric("test.code_coverage.lines_pct") is None
        assert test_module_span.get_metric("test.code_coverage.lines_pct") is None
        assert test_suite_span.get_metric("test.code_coverage.lines_pct") is None
        assert test_span.get_metric("test.code_coverage.lines_pct") is None

    def test_pytest_does_not_report_code_coverage_with_no_cov_flag(self):
        with open("tools.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
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
                    )
                )
            )

        with open("test_tools.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    from tools import add_two_number_list

                    def test_add_two_number_list():
                        a_list = [1,2,3,4,5,6,7,8]
                        b_list = [2,3,4,5,6,7,8,9]
                        actual_output = add_two_number_list(a_list, b_list)

                        assert actual_output == [3,5,7,9,11,13,15,17]
                    """
                    )
                )
            )

        self.testdir.chdir()
        self.inline_run("--ddtrace", "--no-cov")

        spans = self.pop_spans()
        assert len(spans) == 4
        test_span = spans[0]
        test_session_span = spans[1]
        test_module_span = spans[2]
        test_suite_span = spans[3]

        assert test_session_span.get_metric("test.code_coverage.lines_pct") is None
        assert test_module_span.get_metric("test.code_coverage.lines_pct") is None
        assert test_suite_span.get_metric("test.code_coverage.lines_pct") is None
        assert test_span.get_metric("test.code_coverage.lines_pct") is None

    def test_pytest_reports_correct_source_info(self):
        """Tests that decorated functions are reported with correct source file information and with relative to
        repo root
        """
        os.chdir(self.git_repo)
        os.mkdir("nested_dir")
        os.chdir("nested_dir")
        with open("my_decorators.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    def outer_decorator(func):
                         def wrapper(*args, **kwargs):
                            return func(*args, **kwargs)
                         return wrapper

                    @outer_decorator
                    def inner_decorator(func):
                         def wrapper(*args, **kwargs):
                            return func(*args, **kwargs)
                         return wrapper
                    """
                    )
                )
            )

        with open("test_mydecorators.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    # this comment is line 2 and if you didn't know that it'd be easy to miscount below
                    from my_decorators import outer_decorator, inner_decorator
                    from unittest.mock import patch

                    def local_decorator(func):
                        def wrapper(*args, **kwargs):
                            return func(*args, **kwargs)
                        return wrapper

                    def test_one_decorator():  # line 11
                        str1 = "string 1"
                        str2 = "string 2"
                        assert str1 != str2

                    @local_decorator  # line 16
                    def test_local_decorated():
                        str1 = "string 1"
                        str2 = "string 2"
                        assert str1 == str2

                    @patch("ddtrace.config._potato", "potato")  # line 22
                    def test_patched_undecorated():
                        str1 = "string 1"
                        str2 = "string 2"
                        assert str1 != str2

                    @patch("ddtrace.config._potato", "potato")  # line 28
                    @inner_decorator
                    def test_patched_single_decorated():
                        str1 = "string 1"
                        str2 = "string 2"
                        assert str1 == str2

                    @patch("ddtrace.config._potato", "potato")  # line 35
                    @outer_decorator
                    def test_patched_double_decorated():
                        str1 = "string 1"
                        str2 = "string 2"
                        assert str1 != str2

                    @outer_decorator  # line 42
                    @patch("ddtrace.config._potato", "potato")
                    @local_decorator
                    def test_grand_slam():
                        str1 = "string 1"
                        str2 = "string 2"
                        assert str1 == str2
                    """
                    )
                )
            )

        self.inline_run("--ddtrace", project_dir=str(self.git_repo))

        spans = self.pop_spans()
        assert len(spans) == 9
        test_names_to_source_info = {
            span.get_tag("test.name"): (
                span.get_tag("test.source.file"),
                span.get_metric("test.source.start"),
                span.get_metric("test.source.end"),
            )
            for span in spans
            if span.get_tag("type") == "test"
        }
        assert len(test_names_to_source_info) == 6

        expected_path = "nested_dir/test_mydecorators.py"
        expected_source_info = {
            "test_one_decorator": (expected_path, 11, 15),
            "test_local_decorated": (expected_path, 16, 21),
            "test_patched_undecorated": (expected_path, 22, 27),
            "test_patched_single_decorated": (expected_path, 28, 34),
            "test_patched_double_decorated": (expected_path, 35, 41),
            "test_grand_slam": (expected_path, 42, 49),
        }

        assert expected_source_info == test_names_to_source_info

    def test_pytest_without_git_does_not_crash(self):
        with open("tools.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
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
                    )
                )
            )

        with open("test_tools.py", "w+") as fd:
            fd.write(
                textwrap.dedent(
                    (
                        """
                    from tools import add_two_number_list

                    def test_add_two_number_list():
                        a_list = [1,2,3,4,5,6,7,8]
                        b_list = [2,3,4,5,6,7,8,9]
                        actual_output = add_two_number_list(a_list, b_list)

                        assert actual_output == [3,5,7,9,11,13,15,17]
                    """
                    )
                )
            )

        self.testdir.chdir()
        with mock.patch("ddtrace.ext.git._get_executable_path", return_value=None), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig",
            _get_default_civisibility_ddconfig(),
        ) as mock_ddconfig:
            mock_ddconfig._ci_visibility_agentless_enabled = True
            self.inline_run("--ddtrace")

            spans = self.pop_spans()
            assert len(spans) == 4
            test_span = spans[0]
            test_session_span = spans[1]
            test_module_span = spans[2]
            test_suite_span = spans[3]

            assert test_session_span.get_metric("test.code_coverage.lines_pct") is None
            assert test_module_span.get_metric("test.code_coverage.lines_pct") is None
            assert test_suite_span.get_metric("test.code_coverage.lines_pct") is None
            assert test_span.get_metric("test.code_coverage.lines_pct") is None
