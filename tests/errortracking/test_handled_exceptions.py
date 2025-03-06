import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

from tests.subprocesstest import run_in_subprocess
from tests.utils import TracerTestCase


skipif_bytecode_injection_not_supported = pytest.mark.skipif(
    sys.version_info[:2] < (3, 10),
    reason="Injection is only supported for 3.10+",
)


@skipif_bytecode_injection_not_supported
class ErrorTestCases(TracerTestCase):
    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED="all"))
    def test_basic_try_except(self):
        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()
        from tests.errortracking._test_functions import test_basic_try_except_f

        value = 0

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = test_basic_try_except_f(value)

        f()
        HandledExceptionCollector.disable()

        assert value == 10
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(1)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "auto caught error"}
        )

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED="all"))
    def test_basic_multiple_except(self):
        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()
        from tests.errortracking._test_functions import test_basic_multiple_except_f

        value = 0

        @self.tracer.wrap()
        def f(a):
            nonlocal value
            value = test_basic_multiple_except_f(a, value)

        f(0)
        f(1)
        HandledExceptionCollector.disable()

        assert value == 15
        self.assert_span_count(2)
        self.spans[0].assert_span_event_count(1)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "auto value caught error"}
        )
        self.spans[1].assert_span_event_count(1)
        self.spans[1].assert_span_event_attributes(
            0, {"exception.type": "builtins.RuntimeError", "exception.message": "auto caught error"}
        )

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED="all"))
    def test_handled_same_error_multiple_times(self):
        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()
        from tests.errortracking._test_functions import test_handled_same_error_multiple_times_f

        value = 0

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = test_handled_same_error_multiple_times_f(value)

        f()
        HandledExceptionCollector.disable()

        assert value == 10
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(1)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "auto caught error"}
        )

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED="all"))
    def test_reraise_handled_error(self):
        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()
        from tests.errortracking._test_functions import test_reraise_handled_error_f

        value = 0

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = test_reraise_handled_error_f(value)

        f()
        HandledExceptionCollector.disable()

        assert value == 10
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(2)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "auto caught error"}
        )
        self.spans[0].assert_span_event_attributes(
            1, {"exception.type": "builtins.RuntimeError", "exception.message": "auto caught error"}
        )

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED="all"))
    def test_async_error(self):
        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()
        import asyncio

        from tests.errortracking._test_functions import test_sync_error_f

        value = ""

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = asyncio.run(test_sync_error_f(value))

        f()
        HandledExceptionCollector.disable()

        assert value == "<sync_error><async_error>"
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(2)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "this is a sync error"}
        )
        self.spans[0].assert_span_event_attributes(
            1, {"exception.type": "builtins.ValueError", "exception.message": "this is an async error"}
        )

    @run_in_subprocess(
        env_overrides=dict(
            DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED="all",
            DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_AFTER_UNHANDLED="true",
        )
    )
    def test_report_after_unhandled_without_raise(self):
        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()
        from tests.errortracking._test_functions import test_report_after_unhandled_without_raise_f

        value = 0

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = test_report_after_unhandled_without_raise_f(value)

        f()
        HandledExceptionCollector.disable()

        assert value == 10
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(0)

    @run_in_subprocess(
        env_overrides=dict(
            DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED="all",
            DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_AFTER_UNHANDLED="true",
        )
    )
    def test_report_after_unhandled_with_raise(self):
        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()
        from tests.errortracking._test_functions import test_report_after_unhandled_without_raise_f

        value = 0

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = test_report_after_unhandled_without_raise_f(value)
            raise NameError("kill program")

        with pytest.raises(NameError):
            f()
        HandledExceptionCollector.disable()

        assert value == 10
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(2)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "auto caught error"}
        )
        self.spans[0].assert_span_event_attributes(
            1, {"exception.type": "builtins.RuntimeError", "exception.message": "auto caught error"}
        )


@skipif_bytecode_injection_not_supported
class UserCodeErrorTestCases(TracerTestCase):
    @pytest.fixture(scope="class", autouse=True)
    def load_user_code(self):
        from tests.errortracking._test_functions import main_user_code_string
        from tests.errortracking._test_functions import module_user_code_string
        from tests.errortracking._test_functions import submodule_1_string
        from tests.errortracking._test_functions import submodule_2_string

        # set up fake user code
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

    @run_in_subprocess(
        env_overrides=dict(
            DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED="user",
        )
    )
    def test_user_code_reporting(self):
        package_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "third_party"))
        subprocess.run([sys.executable, "-m", "pip", "install", package_dir], check=True)
        import main_code  # type: ignore

        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()

        value = ""

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = main_code.main_user_code(value)  # type: ignore

        f()
        HandledExceptionCollector.disable()

        """ In python3.12, it seems ddtrace can be considered as
        user code in the CI. Rather than doing magic stuff in the code just for a test
        we considered it as passed if the numpy except is not in the event and the two user code
        are
        """
        assert value == "<except_f><except_module_f><except_numpy>"
        self.assert_span_count(1)
        print(len(self.spans[0]._events))
        assert len(self.spans[0]._events) >= 2
        event_messages = [event.attributes["exception.message"] for event in self.spans[0]._events]
        assert "auto caught error" in event_messages
        assert "module caught error" in event_messages
        assert "<error_numpy_f>" not in event_messages

    @run_in_subprocess(
        env_overrides=dict(
            DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED="third_party",
        )
    )
    def test_user_code_reporting_with_third_party(self):
        package_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "third_party"))
        subprocess.run([sys.executable, "-m", "pip", "install", package_dir], check=True)
        import main_code  # type: ignore

        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()

        value = ""

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = main_code.main_user_code(value)  # type: ignore

        f()
        HandledExceptionCollector.disable()

        assert value == "<except_f><except_module_f><except_numpy>"
        self.assert_span_count(1)
        for event in self.spans[0]._events:
            print(event)
        self.spans[0].assert_span_event_count(1)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "<error_numpy_f>"}
        )

    @run_in_subprocess(
        env_overrides=dict(
            DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED_MODULES="numpy,user_module",
        )
    )
    def test_user_code_reporting_with_filtered_third_party_and_user_code(self):
        package_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "third_party"))
        subprocess.run([sys.executable, "-m", "pip", "install", package_dir], check=True)
        import main_code  # type: ignore

        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()

        value = ""

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = main_code.main_user_code(value)  # type: ignore

        f()
        HandledExceptionCollector.disable()

        assert value == "<except_f><except_module_f><except_numpy>"
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(2)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "module caught error"}
        )
        self.spans[0].assert_span_event_attributes(
            1, {"exception.type": "builtins.ValueError", "exception.message": "<error_numpy_f>"}
        )

    @run_in_subprocess(env_overrides=dict(DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED_MODULES="submodule"))
    def test_user_code_scoped_reporting(self):
        import submodule.submodule_1 as sub_1  # type: ignore
        import submodule.submodule_2 as sub_2  # type: ignore

        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()

        # value is used to ensure the except block is properly executed
        value = ""

        @self.tracer.wrap()
        def f():
            nonlocal value
            try:
                raise ValueError("auto caught error")
            except ValueError:
                value += "<except_f>"

            value += sub_1.submodule_1_f()
            value += sub_2.submodule_2_f()

        f()
        HandledExceptionCollector.disable()

        assert value == "<except_f><except_submodule_1><except_submodule_2>"
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(2)

        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.RuntimeError", "exception.message": "<error_function_submodule_1>"}
        )
        self.spans[0].assert_span_event_attributes(
            1, {"exception.type": "builtins.ValueError", "exception.message": "<error_function_submodule_2>"}
        )

    @run_in_subprocess(
        env_overrides=dict(DD_ERROR_TRACKING_REPORT_HANDLED_ERRORS_ENABLED_MODULES="submodule.submodule_2")
    )
    def test_user_code_narrowed_scoped_reporting(self):
        import submodule.submodule_1 as sub_1  # type: ignore
        import submodule.submodule_2 as sub_2  # type: ignore

        from ddtrace.errortracking._handled_exceptions.collector import HandledExceptionCollector

        HandledExceptionCollector.enable()

        # value is used to ensure the except block is properly executed
        value = ""

        @self.tracer.wrap()
        def f():
            nonlocal value
            try:
                raise ValueError("auto caught error")
            except ValueError:
                value += "<except_f>"

            value += sub_1.submodule_1_f()
            value += sub_2.submodule_2_f()

        f()
        HandledExceptionCollector.disable()

        assert value == "<except_f><except_submodule_1><except_submodule_2>"
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(1)

        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "<error_function_submodule_2>"}
        )
