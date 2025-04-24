import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

from tests.subprocesstest import run_in_subprocess
from tests.utils import TracerTestCase


skipif_errortracking_not_supported = pytest.mark.skipif(
    sys.version_info[:2] < (3, 10),
    reason="Injection is only supported for 3.10+",
)


@skipif_errortracking_not_supported
class ErrorTestCases(TracerTestCase):
    """
    This class contains all the cases testing the feature itself (without
    taking into account the configuration)

    The test function are imported because Python3.10 and Python3.11
    can only instrument imported python code.

    The value parameter ensures that the except block are indeed executed
    """

    def _run_error_test(self, test_func, initial_value, expected_value, expected_events):
        """Helper method to run error handling reporting tests."""
        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector
        from tests.errortracking._test_functions import __dict__ as test_functions

        HandledExceptionCollector.enable()

        test_function = test_functions[test_func]
        value = initial_value

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = test_function(value)

        # One the test is raising an exception but we do not want
        # to record the handling of the exception so except block is here
        try:
            f()
        except Exception:
            value = 10

        HandledExceptionCollector.disable()

        assert value == expected_value
        for i, span_events in enumerate(expected_events):
            self.spans[i].assert_span_event_count(len(span_events))
            for j, event_attrs in enumerate(span_events):
                self.spans[i].assert_span_event_attributes(j, event_attrs)

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_HANDLED_ERRORS="all"))
    def test_basic_try_except(self):
        self._run_error_test(
            "test_basic_try_except_f",
            initial_value=0,
            expected_value=10,
            expected_events=[[{"exception.type": "builtins.ValueError", "exception.message": "auto caught error"}]],
        )

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_HANDLED_ERRORS="all"))
    def test_basic_multiple_except(self):
        self._run_error_test(
            "test_basic_multiple_except_f",
            initial_value=0,
            expected_value=15,
            expected_events=[
                [
                    {"exception.type": "builtins.ValueError", "exception.message": "auto value caught error"},
                    {"exception.type": "builtins.RuntimeError", "exception.message": "auto caught error"},
                ],
            ],
        )

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_HANDLED_ERRORS="all"))
    def test_handle_same_error_multiple_times(self):
        self._run_error_test(
            "test_handle_same_error_multiple_times_f",
            initial_value=0,
            expected_value=10,
            expected_events=[[{"exception.type": "builtins.ValueError", "exception.message": "auto caught error"}]],
        )
        # This asserts that the reported span event contained the info
        # of the last time the error is handled
        assert "line 30" in self.spans[0]._events[0].attributes["exception.stacktrace"]

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_HANDLED_ERRORS="all"))
    def test_handled_same_error_different_type(self):
        self._run_error_test(
            "test_handled_same_error_different_type_f",
            initial_value=0,
            expected_value=10,
            expected_events=[
                [
                    {"exception.type": "builtins.ValueError", "exception.message": "auto caught error"},
                    {"exception.type": "builtins.RuntimeError", "exception.message": "auto caught error"},
                ]
            ],
        )

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_HANDLED_ERRORS="all"))
    def test_handled_then_raise_error(self):
        self._run_error_test(
            "test_handled_then_raise_error_f",
            initial_value=0,
            expected_value=10,
            expected_events=[[]],
        )

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_HANDLED_ERRORS="all"))
    def test_more_handled_than_collector_capacity(self):
        self._run_error_test(
            "test_more_handled_than_collector_capacity_f",
            initial_value=0,
            expected_value=101,
            expected_events=[
                [{"exception.type": "builtins.ValueError", "exception.message": "auto caught error"}] * 100
            ],
        )

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_HANDLED_ERRORS="all"))
    def test_async_error(self):
        self._run_error_test(
            "test_asyncio_error_f",
            initial_value="",
            expected_value="<sync_error><async_error>",
            expected_events=[
                [
                    {"exception.type": "builtins.ValueError", "exception.message": "this is a sync error"},
                    {"exception.type": "builtins.ValueError", "exception.message": "this is an async error"},
                ]
            ],
        )

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_HANDLED_ERRORS="all"))
    def test_handled_in_parent_span(self):
        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector
        from tests.errortracking._test_functions import handled_in_parent_span_f

        HandledExceptionCollector.enable()

        value = 0
        value = handled_in_parent_span_f(value, self.tracer)

        HandledExceptionCollector.disable()

        assert value == 1
        assert self.spans[0].name == "parent_span"
        assert len(self.spans[0]._events) == 1
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "auto caught error"}
        )
        assert "line 72" in self.spans[0]._events[0].attributes["exception.stacktrace"]

        assert self.spans[1].name == "child_span"
        assert len(self.spans[1]._events) == 0


@skipif_errortracking_not_supported
class UserCodeErrorTestCases(TracerTestCase):
    """
    This class contains all the tests testing the filtering capabilities of the feature
    To do so, we will create fake user code and a fake third party packages to reproduce
    a real environment
    """

    @pytest.fixture(scope="class", autouse=True)
    def load_user_code(self):
        from tests.errortracking._test_functions import main_user_code_string
        from tests.errortracking._test_functions import module_user_code_string
        from tests.errortracking._test_functions import submodule_1_string
        from tests.errortracking._test_functions import submodule_2_string

        # Setting up fake user code
        temp_dir = tempfile.TemporaryDirectory()
        base_path = pathlib.Path(temp_dir.name)
        base_path.mkdir(exist_ok=True)

        module_code_path = os.path.join(base_path, "user_module.py")
        with open(module_code_path, "w") as file:
            file.write(module_user_code_string)

        main_code_path = os.path.join(base_path, "main_code.py")
        with open(main_code_path, "w") as file:
            file.write(main_user_code_string)

        # Creation of submodule
        submodule_path = os.path.join(base_path, "submodule")
        pathlib.Path(submodule_path).mkdir(exist_ok=True)
        init_path = os.path.join(submodule_path, "__init__.py")
        with open(init_path, "w") as file:
            file.write("")

        submodule_1_path = os.path.join(submodule_path, "submodule_1.py")
        with open(submodule_1_path, "w") as file:
            file.write(submodule_1_string)

        submodule_2_path = os.path.join(submodule_path, "submodule_2.py")
        with open(submodule_2_path, "w") as file:
            file.write(submodule_2_string)

        sys.path.insert(0, str(base_path))
        os.environ["PYTHONPATH"] = str(base_path) + os.pathsep + os.environ.get("PYTHONPATH", "")

        self.addCleanup(temp_dir.cleanup)

    def _run_user_code_test(
        self, initial_value, expected_value, expected_events, use_maincode=True, use_submodules=False
    ):
        """Helper method to run filtering tests."""

        # import our fake third party package
        package_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "third_party"))
        subprocess.run([sys.executable, "-m", "pip", "install", package_dir], check=True)

        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()

        value = initial_value

        @self.tracer.wrap()
        def f():
            nonlocal value
            if use_submodules:
                try:
                    raise ValueError("auto caught error")
                except ValueError:
                    value += "<except_f>"
            if use_maincode:
                import main_code  # type: ignore

                value = main_code.main_user_code(value)  # type: ignore

            if use_submodules:
                import submodule.submodule_1 as sub_1  # type: ignore
                import submodule.submodule_2 as sub_2  # type: ignore

                value += sub_1.submodule_1_f()  # type: ignore
                value += sub_2.submodule_2_f()  # type: ignore

        f()
        HandledExceptionCollector.disable()

        assert value == expected_value
        self.assert_span_count(1)

        # Depending on CI vs local tests, it is not clear if ddtrace is considered
        # as user code or not, so we cannot assume the exact number of span events
        # We will just assert the message that should be present or absent
        assert len(self.spans[0]._events) >= expected_events.get("min_events", 1)
        event_messages = [event.attributes["exception.message"] for event in self.spans[0]._events]
        for message in expected_events.get("messages_present", []):
            assert message in event_messages
        for message in expected_events.get("messages_absent", []):
            assert message not in event_messages

    @run_in_subprocess(
        env_overrides=dict(
            DD_ERROR_TRACKING_HANDLED_ERRORS="user",
        )
    )
    def test_user_code_reporting(self):
        self._run_user_code_test(
            initial_value="",
            expected_value="<except_f><except_module_f><except_numpy>",
            expected_events={
                "min_events": 2,
                "messages_present": ["auto caught error", "module caught error"],
                "messages_absent": ["<error_numpy_f>"],
            },
        )

    @run_in_subprocess(
        env_overrides=dict(
            DD_ERROR_TRACKING_HANDLED_ERRORS="third_party",
        )
    )
    def test_user_code_reporting_with_third_party(self):
        self._run_user_code_test(
            initial_value="",
            expected_value="<except_f><except_module_f><except_numpy>",
            expected_events={"min_events": 1, "messages_present": ["<error_numpy_f>"]},
        )

    @run_in_subprocess(
        env_overrides=dict(
            DD_ERROR_TRACKING_HANDLED_ERRORS_MODULES="numpy,user_module",
        )
    )
    def test_user_code_reporting_with_filtered_third_party_and_user_code(self):
        self._run_user_code_test(
            initial_value="",
            expected_value="<except_f><except_module_f><except_numpy>",
            expected_events={"min_events": 2, "messages_present": ["module caught error", "<error_numpy_f>"]},
        )

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_HANDLED_ERRORS_MODULES="submodule"))
    def test_user_code_scoped_reporting(self):
        self._run_user_code_test(
            initial_value="",
            expected_value="<except_f><except_submodule_1><except_submodule_2>",
            expected_events={
                "min_events": 2,
                "messages_present": ["<error_function_submodule_1>", "<error_function_submodule_2>"],
            },
            use_maincode=False,
            use_submodules=True,
        )

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_HANDLED_ERRORS_MODULES="submodule.submodule_2"))
    def test_user_code_narrowed_scoped_reporting(self):
        self._run_user_code_test(
            initial_value="",
            expected_value="<except_f><except_submodule_1><except_submodule_2>",
            expected_events={"min_events": 1, "messages_present": ["<error_function_submodule_2>"]},
            use_maincode=False,
            use_submodules=True,
        )
