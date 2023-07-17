import json
import os

import pytest

import ddtrace
from ddtrace.constants import ERROR_MSG
from ddtrace.contrib.pytest.plugin import is_enabled
from ddtrace.ext import test
from ddtrace.internal.ci_visibility import CIVisibility
from tests.utils import DummyCIVisibilityWriter
from tests.utils import TracerTestCase
from tests.utils import override_env


_SIMPLE_SCENARIO = """
Feature: Simple feature
    Scenario: Simple scenario
        Given I have a bar
        When I eat it
        Then I don't have a bar
"""


class TestPytest(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch):
        self.testdir = testdir
        self.monkeypatch = monkeypatch

    def inline_run(self, *args):
        """Execute test script with test tracer."""

        class CIVisibilityPlugin:
            @staticmethod
            def pytest_configure(config):
                if is_enabled(config):
                    assert CIVisibility.enabled
                    CIVisibility.disable()
                    CIVisibility.enable(tracer=self.tracer, config=ddtrace.config.pytest)

        with override_env(dict(DD_API_KEY="foobar.baz")):
            self.tracer.configure(writer=DummyCIVisibilityWriter("https://citestcycle-intake.banana"))
            return self.testdir.inline_run(*args, plugins=[CIVisibilityPlugin()])

    def subprocess_run(self, *args):
        """Execute test script with test tracer."""
        return self.testdir.runpytest_subprocess(*args)

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
        self.inline_run("--ddtrace", file_name)
        spans = self.pop_spans()

        assert len(spans) == 12  # 3 scenarios + 7 steps
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
        self.inline_run("--ddtrace", file_name)
        spans = self.pop_spans()

        assert len(spans) == 6
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
        self.inline_run("--ddtrace", file_name)
        spans = self.pop_spans()

        assert len(spans) == 6
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
        self.inline_run("--ddtrace", file_name)
        spans = self.pop_spans()

        assert len(spans) == 3
        assert spans[0].get_tag(ERROR_MSG)
