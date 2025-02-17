import os
import pathlib
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
    @run_in_subprocess(env_overrides=dict(DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS="true"))
    def test_basic_try_except(self):
        import ddtrace.internal.error_reporting.handled_exceptions  # noqa: F401
        from tests.internal.error_reporting.test_functions import test_basic_try_except_f

        value = 0

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = test_basic_try_except_f(value)

        f()

        assert value == 10
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(1)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "auto caught error"}
        )

    @run_in_subprocess(env_overrides=dict(DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS="true"))
    def test_basic_multiple_except(self):
        import ddtrace.internal.error_reporting.handled_exceptions  # noqa: F401
        from tests.internal.error_reporting.test_functions import test_basic_multiple_except_f

        value = 0

        @self.tracer.wrap()
        def f(a):
            nonlocal value
            value = test_basic_multiple_except_f(a, value)

        f(0)
        f(1)
        assert value == 15
        self.assert_span_count(2)
        self.spans[0].assert_span_event_count(1)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "auto value caught error"}
        )
        self.spans[1].assert_span_event_count(1)
        self.spans[1].assert_span_event_attributes(
            0, {"exception.type": "builtins.Exception", "exception.message": "auto caught error"}
        )

    @run_in_subprocess(env_overrides=dict(DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS="true"))
    def test_handled_same_error_multiple_times(self):
        import ddtrace.internal.error_reporting.handled_exceptions  # noqa: F401
        from tests.internal.error_reporting.test_functions import test_handled_same_error_multiple_times_f

        value = 0

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = test_handled_same_error_multiple_times_f(value)

        f()

        assert value == 10
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(1)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "auto caught error"}
        )

    @run_in_subprocess(env_overrides=dict(DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS="true"))
    def test_reraise_handled_error(self):
        import ddtrace.internal.error_reporting.handled_exceptions  # noqa: F401
        from tests.internal.error_reporting.test_functions import test_reraise_handled_error_f

        value = 0

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = test_reraise_handled_error_f(value)

        f()

        assert value == 10
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(2)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "auto caught error"}
        )
        self.spans[0].assert_span_event_attributes(
            1, {"exception.type": "builtins.RuntimeError", "exception.message": "auto caught error"}
        )

    @run_in_subprocess(env_overrides=dict(DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS="true"))
    def test_async_error(self):
        import asyncio

        import ddtrace.internal.error_reporting.handled_exceptions  # noqa: F401
        from tests.internal.error_reporting.test_functions import test_sync_error_f

        value = ""

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = asyncio.run(test_sync_error_f(value))

        f()

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
            DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS_MODULES="tests.internal.error_reporting.module.submodule"
        )
    )
    def test_user_code_module_scoped_reporting(self):
        import ddtrace.internal.error_reporting.handled_exceptions  # noqa: F401
        from tests.internal.error_reporting.module.sample_module import module_func
        from tests.internal.error_reporting.module.submodule.sample_submodule_1 import submodule_1
        from tests.internal.error_reporting.module.submodule.sample_submodule_2 import submodule_2

        # value is used to ensure the except block is properly executed
        value = ""

        @self.tracer.wrap()
        def f():
            nonlocal value
            value += module_func()
            value += submodule_1()
            value += submodule_2()

        f()
        assert value == "<except_module><except_submodule_1><except_submodule_2>"
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(2)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.RuntimeError", "exception.message": "<error_function_submodule_1>"}
        )
        self.spans[0].assert_span_event_attributes(
            1, {"exception.type": "builtins.ValueError", "exception.message": "<error_function_submodule_2>"}
        )

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS_MODULES="tests.internal.error_reporting.module.submodule.sample_submodule_2"
        )
    )
    def test_user_code_narrowed_scoped_reporting(self):
        import ddtrace.internal.error_reporting.handled_exceptions  # noqa: F401
        from tests.internal.error_reporting.module.submodule.sample_submodule_1 import submodule_1
        from tests.internal.error_reporting.module.submodule.sample_submodule_2 import submodule_2

        # value is used to ensure the except block is properly executed
        value = ""

        @self.tracer.wrap()
        def f():
            nonlocal value
            try:
                raise ValueError("auto caught error")
            except ValueError:
                value += "<except_f>"

            value += submodule_1()
            value += submodule_2()

        f()
        assert value == "<except_f><except_submodule_1><except_submodule_2>"
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(1)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "<error_function_submodule_2>"}
        )

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS="true",
            DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS_AFTER_UNHANDLED="true",
        )
    )
    def test_report_after_unhandled_without_raise(self):
        import ddtrace.internal.error_reporting.handled_exceptions  # noqa: F401s
        from tests.internal.error_reporting.test_functions import test_report_after_unhandled_without_raise_f

        value = 0

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = test_report_after_unhandled_without_raise_f(value)

        f()

        assert value == 10
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(0)

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS="true",
            DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS_AFTER_UNHANDLED="true",
        )
    )
    def test_report_after_unhandled_with_raise(self):
        import ddtrace.internal.error_reporting.handled_exceptions  # noqa: F401
        from tests.internal.error_reporting.test_functions import test_report_after_unhandled_without_raise_f

        value = 0

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = test_report_after_unhandled_without_raise_f(value)
            raise NameError("kill program")

        with pytest.raises(NameError):
            f()

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
        from tests.internal.error_reporting.test_functions import main_user_code_string
        from tests.internal.error_reporting.test_functions import module_user_code_string

        temp_dir = tempfile.TemporaryDirectory()
        base_path = pathlib.Path(temp_dir.name)
        base_path.mkdir(exist_ok=True)

        module_code_path = os.path.join(base_path, "user_module.py")
        with open(module_code_path, "w") as file:
            file.write(module_user_code_string)

        main_code_path = os.path.join(base_path, "main_code.py")
        with open(main_code_path, "w") as file:
            file.write(main_user_code_string)
        os.environ["PYTHONPATH"] = str(base_path) + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.addCleanup(temp_dir.cleanup)

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS_USER="true",
        )
    )
    def test_user_code_reporting(self):
        import main_code  # type: ignore

        import ddtrace.internal.error_reporting.handled_exceptions  # noqa: F401

        value = ""

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = main_code.main_user_code(value)  # type: ignore

        f()

        assert value == "<except_f><except_module_f><except_submodule_1>"
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(2)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "auto caught error"}
        )
        self.spans[0].assert_span_event_attributes(
            1, {"exception.type": "builtins.ValueError", "exception.message": "module caught error"}
        )

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS_THIRD_PARTY="true",
        )
    )
    def test_user_code_reporting_with_third_party(self):
        import main_code  # type: ignore

        import ddtrace.internal.error_reporting.handled_exceptions  # noqa: F401

        value = ""

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = main_code.main_user_code(value)  # type: ignore

        f()

        assert value == "<except_f><except_module_f><except_submodule_1>"
        self.assert_span_count(1)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.RuntimeError", "exception.message": "<error_function_submodule_1>"}
        )

    @run_in_subprocess(
        env_overrides=dict(
            DD_TRACE_EXPERIMENTAL_REPORTED_HANDLED_EXCEPTIONS_MODULES="tests.internal.error_reporting,user_module",
        )
    )
    def test_user_code_reporting_with_filtered_third_party_and_user_code(self):
        import main_code  # type: ignore

        import ddtrace.internal.error_reporting.handled_exceptions  # noqa: F401

        value = ""

        @self.tracer.wrap()
        def f():
            nonlocal value
            value = main_code.main_user_code(value)  # type: ignore

        f()

        assert value == "<except_f><except_module_f><except_submodule_1>"
        self.assert_span_count(1)
        self.spans[0].assert_span_event_count(2)
        self.spans[0].assert_span_event_attributes(
            0, {"exception.type": "builtins.ValueError", "exception.message": "module caught error"}
        )
        self.spans[0].assert_span_event_attributes(
            1, {"exception.type": "builtins.RuntimeError", "exception.message": "<error_function_submodule_1>"}
        )
