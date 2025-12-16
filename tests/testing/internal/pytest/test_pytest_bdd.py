from __future__ import annotations

import json
import os
from unittest.mock import Mock
from unittest.mock import patch

from _pytest.pytester import Pytester
import pytest

from ddtrace.constants import ERROR_MSG
from ddtrace.ext import test
from ddtrace.testing.internal.pytest.bdd import BddTestOptPlugin
from ddtrace.testing.internal.pytest.bdd import _get_step_func_args_json
from tests.contrib.patch import emit_integration_and_version_to_test_agent
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


_SIMPLE_SCENARIO = """
Feature: Simple feature
    Scenario: Simple scenario
        Given I have a bar
        When I eat it
        Then I don't have a bar
"""


class TestPytestBdd:
    @pytest.mark.xfail(raises=ConnectionRefusedError, reason="test agent is down")
    def test_and_emit_get_version(self) -> None:
        plugin = BddTestOptPlugin(Mock())
        version = plugin._get_framework_version()
        assert isinstance(version, str)
        assert version != ""
        emit_integration_and_version_to_test_agent("pytest-bdd", version)

    def test_pytest_bdd_scenario_with_parameters(self, pytester: Pytester) -> None:
        """Test that pytest-bdd traces scenario with all steps."""
        pytester.makefile(
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
        py_file = pytester.makepyfile(
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
        file_name = os.path.basename(str(py_file))
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                pytester.inline_run("-p", "no:randomly", "--ddtrace", file_name)

        events = list(event_capture.events())

        assert len(events) == 13  # 3 scenarios + 7 steps + 1 module
        assert json.loads(events[0]["content"]["meta"].get(test.PARAMETERS)) == {"bars": 0}
        assert json.loads(events[2]["content"]["meta"].get(test.PARAMETERS)) == {"bars": -1}
        assert json.loads(events[4]["content"]["meta"].get(test.PARAMETERS)) == {"bars": 2}
        assert json.loads(events[6]["content"]["meta"].get(test.PARAMETERS)) == {"bars": 0}
        assert json.loads(events[8]["content"]["meta"].get(test.PARAMETERS)) == {"bars": "no"}

    def test_pytest_bdd_scenario(self, pytester: Pytester) -> None:
        """Test that pytest-bdd traces scenario with all steps."""
        pytester.makefile(
            ".feature",
            simple=_SIMPLE_SCENARIO,
        )
        py_file = pytester.makepyfile(
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
        file_name = os.path.basename(str(py_file))
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                pytester.inline_run("-p", "no:randomly", "--ddtrace", file_name)

        events = list(event_capture.events())

        assert len(events) == 7
        assert events[0]["content"]["resource"] == "I have a bar"
        assert events[0]["content"]["name"] == "given"
        assert events[1]["content"]["resource"] == "I eat it"
        assert events[1]["content"]["name"] == "when"
        assert events[2]["content"]["resource"] == "I don't have a bar"
        assert events[2]["content"]["name"] == "then"

        # ꙮꙮꙮassert events[3]["content"]["meta"].get("component") == "pytest"
        assert events[3]["content"]["meta"].get("test.name") == "Simple scenario"
        assert events[3]["type"] == "test"

    def test_pytest_bdd_scenario_with_failed_step(self, pytester: Pytester) -> None:
        """Test that pytest-bdd traces scenario with a failed step."""
        pytester.makefile(
            ".feature",
            simple=_SIMPLE_SCENARIO,
        )
        py_file = pytester.makepyfile(
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
        file_name = os.path.basename(str(py_file))
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                pytester.inline_run("-p", "no:randomly", "--ddtrace", file_name)

        events = list(event_capture.events())

        assert len(events) == 7
        assert events[2]["content"]["name"] == "then"
        assert events[2]["content"]["meta"].get(ERROR_MSG) == "assert 0 == -1"

    def test_pytest_bdd_with_missing_step_implementation(self, pytester: Pytester) -> None:
        """Test that pytest-bdd captures missing steps."""
        pytester.makefile(
            ".feature",
            simple=_SIMPLE_SCENARIO,
        )
        py_file = pytester.makepyfile(
            """
            from pytest_bdd import scenario, given, then, when

            @scenario("simple.feature", "Simple scenario")
            def test_simple():
                pass
            """
        )
        file_name = os.path.basename(str(py_file))
        with (
            patch(
                "ddtrace.testing.internal.session_manager.APIClient",
                return_value=mock_api_client_settings(),
            ),
            setup_standard_mocks(),
        ):
            with EventCapture.capture() as event_capture:
                pytester.inline_run("-p", "no:randomly", "--ddtrace", file_name)

        events = list(event_capture.events())

        assert len(events) == 4
        assert "Step definition is not found" in events[0]["content"]["meta"].get(ERROR_MSG)

    def test_get_step_func_args_json_empty(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("ddtrace.testing.internal.pytest.bdd._extract_step_func_args", lambda *args: None)

        assert _get_step_func_args_json(None, lambda: None, None) is None

    def test_get_step_func_args_json_valid(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "ddtrace.testing.internal.pytest.bdd._extract_step_func_args",
            lambda *args: {"func_arg": "test string"},
        )

        assert _get_step_func_args_json(None, lambda: None, None) == '{"func_arg": "test string"}'

    def test_get_step_func_args_json_invalid(self, pytester: Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "ddtrace.testing.internal.pytest.bdd._extract_step_func_args", lambda *args: {"func_arg": set()}
        )

        expected = '{"error_serializing_args": "Object of type set is not JSON serializable"}'

        assert _get_step_func_args_json(None, lambda: None, None) == expected
