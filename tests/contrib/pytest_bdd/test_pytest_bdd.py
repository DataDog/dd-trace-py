import os

import pytest

from ddtrace import Pin
from tests.utils import TracerTestCase


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

        class PinTracer:
            @staticmethod
            def pytest_configure(config):
                if Pin.get_from(config) is not None:
                    Pin.override(config, tracer=self.tracer)

        return self.testdir.inline_run(*args, plugins=[PinTracer()])

    def subprocess_run(self, *args):
        """Execute test script with test tracer."""
        return self.testdir.runpytest_subprocess(*args)

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

        assert len(spans) == 4
        assert spans[0].get_tag("test.name") == "Simple scenario"
        assert spans[0].span_type == "test"
        assert spans[1].resource == "I have a bar"
        assert spans[1].span_type == "given"
        assert spans[2].resource == "I eat it"
        assert spans[2].span_type == "when"
        assert spans[3].resource == "I don't have a bar"
        assert spans[3].span_type == "then"

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

        assert len(spans) == 4
        assert spans[-1].span_type == "then"
        assert spans[-1].get_tag("error.msg")

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

        assert len(spans) == 1
        assert spans[0].get_tag("error.msg")
