import json
import os

import pytest

from ddtrace.constants import ERROR_MSG
from ddtrace.contrib.internal.pytest_bdd._plugin import _get_step_func_args_json
from ddtrace.contrib.internal.pytest_bdd._plugin import get_version
from ddtrace.ext import test
from tests.contrib.patch import emit_integration_and_version_to_test_agent
from tests.contrib.pytest.test_pytest import PytestTestCaseBase


_SIMPLE_SCENARIO = """
Feature: Simple feature
    Scenario: Simple scenario
        Given I have a bar
        When I eat it
        Then I don't have a bar
"""


class TestPytest(PytestTestCaseBase):
    def test_and_emit_get_version(self):
        version = get_version()
        assert isinstance(version, str)
        assert version != ""

        emit_integration_and_version_to_test_agent("pytest-bdd", version)

    @pytest.mark.no_getattr_patch
    def test_pytest_bdd_scenario_with_parameters(self):
        """Test that pytest-bdd traces scenario with all steps."""
        self.testdir.makefile(
            ".feature",
            parameters="""
                Feature: Parameters
                    Scenario: Passing scenario
                        Given I have 0 bars
                        When I eat it
                        Then I have -1 bars

                    Scenario: Failing scenario
                        Given I have 2 bars
                        When I eat it
                        Then I have 0 bar

                    Scenario: Failing converter
                        Given I have no bar
                """,
        )
        py_file = self.testdir.makepyfile(
            """
            from pytest_bdd import scenarios, given, then, when, parsers

            scenarios("parameters.feature")

            BAR = None

            @given(parsers.re("^I have (?P<bars>[^ ]+) bar$"))  # loose regex
            def have_simple(bars):
                global BAR
                BAR = bars

            @given(parsers.re("^I have (?P<bars>\\d+) bars$"), converters=dict(bars=int))
            def have(bars):
                global BAR
                BAR = bars

            @when("I eat it")
            def eat():
                global BAR
                BAR -= 1

            @then(parsers.parse("I have {bars:d} bar"))
            def check_parse(bars):
                assert BAR == bars

            @then(parsers.cfparse("I have {bars:d} bars"))
            def check_cfparse(bars):
                assert BAR == bars
            """
        )
        file_name = os.path.basename(py_file.strpath)
        self.inline_run("-p", "no:randomly", "--ddtrace", file_name)
        spans = self.pop_spans()

        assert len(spans) == 13  # 3 scenarios + 7 steps + 1 module
        assert json.loads(spans[1].get_tag(test.PARAMETERS)) == {"bars": 0}
        assert json.loads(spans[3].get_tag(test.PARAMETERS)) == {"bars": -1}
        assert json.loads(spans[5].get_tag(test.PARAMETERS)) == {"bars": 2}
        assert json.loads(spans[7].get_tag(test.PARAMETERS)) == {"bars": 0}
        assert json.loads(spans[9].get_tag(test.PARAMETERS)) == {"bars": "no"}

    def test_pytest_bdd_scenario(self):
        """Test that pytest-bdd traces scenario with all steps."""
        self.testdir.makefile(
            ".feature",
            simple=_SIMPLE_SCENARIO,
        )
        py_file = self.testdir.makepyfile(
            """
            from pytest_bdd import scenario, given, then, when

            @scenario("simple.feature", "Simple scenario")
            def test_simple():
                pass

            BAR = None

            @given("I have a bar")
            def bar():
                global BAR
                BAR = 1

            @when("I eat it")
            def eat():
                global BAR
                BAR -= 1

            @then("I don't have a bar")
            def check():
                assert BAR == 0
            """
        )
        file_name = os.path.basename(py_file.strpath)
        self.inline_run("-p", "no:randomly", "--ddtrace", file_name)
        spans = self.pop_spans()

        assert len(spans) == 7
        assert spans[0].get_tag("component") == "pytest"
        assert spans[0].get_tag("test.name") == "Simple scenario"
        assert spans[0].span_type == "test"
        assert spans[1].resource == "I have a bar"
        assert spans[1].name == "given"
        assert spans[2].resource == "I eat it"
        assert spans[2].name == "when"
        assert spans[3].resource == "I don't have a bar"
        assert spans[3].name == "then"

    def test_pytest_bdd_scenario_with_failed_step(self):
        """Test that pytest-bdd traces scenario with a failed step."""
        self.testdir.makefile(
            ".feature",
            simple=_SIMPLE_SCENARIO,
        )
        py_file = self.testdir.makepyfile(
            """
            from pytest_bdd import scenario, given, then, when

            @scenario("simple.feature", "Simple scenario")
            def test_simple():
                pass

            BAR = None

            @given("I have a bar")
            def bar():
                global BAR
                BAR = 1

            @when("I eat it")
            def eat():
                global BAR
                BAR -= 1

            @then("I don't have a bar")
            def check():
                assert BAR == -1
            """
        )
        file_name = os.path.basename(py_file.strpath)
        self.inline_run("-p", "no:randomly", "--ddtrace", file_name)
        spans = self.pop_spans()

        assert len(spans) == 7
        assert spans[3].name == "then"
        assert spans[3].get_tag(ERROR_MSG)

    def test_pytest_bdd_with_missing_step_implementation(self):
        """Test that pytest-bdd captures missing steps."""
        self.testdir.makefile(
            ".feature",
            simple=_SIMPLE_SCENARIO,
        )
        py_file = self.testdir.makepyfile(
            """
            from pytest_bdd import scenario, given, then, when

            @scenario("simple.feature", "Simple scenario")
            def test_simple():
                pass
            """
        )
        file_name = os.path.basename(py_file.strpath)
        self.inline_run("-p", "no:randomly", "--ddtrace", file_name)
        spans = self.pop_spans()

        assert len(spans) == 4
        assert spans[0].get_tag(ERROR_MSG)

    def test_get_step_func_args_json_empty(self):
        self.monkeypatch.setattr(
            "ddtrace.contrib.internal.pytest_bdd._plugin._extract_step_func_args", lambda *args: None
        )

        assert _get_step_func_args_json(None, lambda: None, None) is None

    def test_get_step_func_args_json_valid(self):
        self.monkeypatch.setattr(
            "ddtrace.contrib.internal.pytest_bdd._plugin._extract_step_func_args",
            lambda *args: {"func_arg": "test string"},
        )

        assert _get_step_func_args_json(None, lambda: None, None) == '{"func_arg": "test string"}'

    def test_get_step_func_args_json_invalid(self):
        self.monkeypatch.setattr(
            "ddtrace.contrib.internal.pytest_bdd._plugin._extract_step_func_args", lambda *args: {"func_arg": set()}
        )

        expected = '{"error_serializing_args": "Object of type set is not JSON serializable"}'

        assert _get_step_func_args_json(None, lambda: None, None) == expected
