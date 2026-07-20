from __future__ import annotations

import json
import os
from unittest.mock import Mock

from _pytest.pytester import Pytester
import pytest

from ddtrace.testing.internal.pytest.bdd import BddTestOptPlugin
from ddtrace.testing.internal.pytest.bdd import _get_step_func_args_json
from tests.contrib.patch import emit_integration_and_version_to_test_agent


_SIMPLE_SCENARIO = """
Feature: Simple feature
    Scenario: Simple scenario
        Given I have a bar
        When I eat it
        Then I don't have a bar
"""


_CAPTURE_PATH_ENV = "_DD_PYTEST_BDD_CAPTURE_PATH"

# AIDEV-NOTE: This plugin installs mocks at import time so they are active before the
# child process initializes the Datadog pytest plugin. Keep these tests out of inline_run:
# nested in-process pytest-cov sessions can corrupt the outer session's coverage state.
_INFRA_PLUGIN = f"""\
import json
import os
from pathlib import Path
from unittest.mock import patch

import ddtrace
import pytest

from ddtrace.constants import ERROR_MSG
from ddtrace.ext import test
from ddtrace.testing.internal.pytest.bdd import STEP_KIND
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


_step_spans = []
_original_start_span = ddtrace.tracer.start_span


def start_span(*args, **kwargs):
    span = _original_start_span(*args, **kwargs)
    if kwargs.get("span_type") == STEP_KIND:
        _step_spans.append(span)
    return span

_span_patch = patch.object(ddtrace.tracer, "start_span", side_effect=start_span)
_span_patch.start()
_api_client_patch = patch(
    "ddtrace.testing.internal.session_manager.APIClient",
    return_value=mock_api_client_settings(),
)
_api_client_patch.start()
_standard_mocks = setup_standard_mocks()
_standard_mocks.__enter__()
_event_capture_context = EventCapture.capture()
_event_capture = _event_capture_context.__enter__()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session):
    events = []
    for event in _event_capture.events():
        meta = event["content"].get("meta", {{}})
        events.append(
            {{
                "type": event["type"],
                "status": meta.get(test.STATUS),
                "name": meta.get(test.NAME),
                "error_msg": meta.get(ERROR_MSG),
            }}
        )

    step_spans = [
        {{
            "name": span.name,
            "resource": span.resource,
            "parameters": span.get_tag(test.PARAMETERS),
            "error_msg": span.get_tag(ERROR_MSG),
        }}
        for span in _step_spans
    ]
    capture = {{"events": events, "step_spans": step_spans}}
    Path(os.environ["{_CAPTURE_PATH_ENV}"]).write_text(json.dumps(capture))
"""


def _run_bdd_subprocess(pytester: Pytester, file_name: str):
    capture_path = pytester.path / "bdd_capture.json"
    pytester.makepyfile(pytest_bdd_infra=_INFRA_PLUGIN)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv(_CAPTURE_PATH_ENV, str(capture_path))
        result = pytester.runpytest_subprocess("-p", "no:randomly", "--ddtrace", "-p", "pytest_bdd_infra", file_name)

    return result, json.loads(capture_path.read_text())


class TestPytestBdd:
    @pytest.fixture(autouse=True)
    def clear_xdist_worker_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
        monkeypatch.delenv("PYTEST_XDIST_TESTRUNUID", raising=False)

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
        result, capture = _run_bdd_subprocess(pytester, file_name)

        result.assert_outcomes(passed=2, failed=1)
        test_events = [event for event in capture["events"] if event["type"] == "test"]

        assert len(test_events) == 3
        assert [event["status"] for event in test_events] == ["pass", "fail", "pass"]
        assert len(capture["step_spans"]) == 7
        assert [json.loads(span["parameters"]) for span in capture["step_spans"] if span["parameters"]] == [
            {"bars": 0},
            {"bars": -1},
            {"bars": 2},
            {"bars": 0},
            {"bars": "no"},
        ]

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
        result, capture = _run_bdd_subprocess(pytester, file_name)

        result.assert_outcomes(passed=1)
        test_events = [event for event in capture["events"] if event["type"] == "test"]

        assert len(test_events) == 1
        assert test_events[0]["name"] == "Simple scenario"
        assert [(span["name"], span["resource"]) for span in capture["step_spans"]] == [
            ("given", "I have a bar"),
            ("when", "I eat it"),
            ("then", "I don't have a bar"),
        ]

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
        result, capture = _run_bdd_subprocess(pytester, file_name)

        result.assert_outcomes(failed=1)
        test_events = [event for event in capture["events"] if event["type"] == "test"]

        assert len(test_events) == 1
        assert test_events[0]["error_msg"] == "assert 0 == -1"
        assert [(span["name"], span["resource"]) for span in capture["step_spans"]] == [
            ("given", "I have a bar"),
            ("when", "I eat it"),
            ("then", "I don't have a bar"),
        ]
        assert capture["step_spans"][-1]["error_msg"] == "assert 0 == -1"

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
        result, capture = _run_bdd_subprocess(pytester, file_name)

        result.assert_outcomes(failed=1)
        assert len(capture["events"]) == 4
        assert "Step definition is not found" in capture["events"][0]["error_msg"]
        assert capture["step_spans"] == []

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
