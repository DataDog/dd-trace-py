import asyncio
import os
from typing import List
from typing import Tuple

import pytest

from tests.integration.utils import AGENT_VERSION


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")


@pytest.mark.parametrize(
    "dd_trace_methods,expected_output",
    [
        ("", []),
        ("module:method1", [("module", "method1")]),
        ("module:method1,method2", [("module", "method1"), ("module", "method2")]),
        (
            "module:method1,method2;mod2:m1,m2",
            [("module", "method1"), ("module", "method2"), ("mod2", "m1"), ("mod2", "m2")],
        ),
        ("mod.submod:m1,m2,m3", [("mod.submod", "m1"), ("mod.submod", "m2"), ("mod.submod", "m3")]),
        ("mod.submod.subsubmod:m1,m2", [("mod.submod.subsubmod", "m1"), ("mod.submod.subsubmod", "m2")]),
        (
            "mod.mod2.mod3:Class.test_method,Class.test_method2",
            [("mod.mod2.mod3", "Class.test_method"), ("mod.mod2.mod3", "Class.test_method2")],
        ),
        ("module", []),
        ("module.", []),
        ("module.method", []),
        ("module.method;module.method", []),
    ],
)
def test_trace_methods_parse(dd_trace_methods: str, expected_output: List[Tuple[str, str]]):
    from ddtrace.internal.tracemethods import _parse_trace_methods

    assert _parse_trace_methods(dd_trace_methods) == expected_output


def _test_method():
    pass


def _test_method2():
    _test_method()


class _Class:
    def test_method(self):
        pass

    def test_method2(self):
        self.test_method()

    async def async_test_method(self):
        await asyncio.sleep(0.01)

    async def async_test_method2(self):
        await self.async_test_method()

    async def _nested_async_test_method(self):
        await asyncio.sleep(0.01)
        # Call other async method to confirm nested spans work
        await _async_test_method()
        await _async_test_method2()

    class NestedClass:
        def test_method(self):
            pass


@pytest.mark.snapshot()
@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_TRACE_METHODS=(
            "tests.integration.test_tracemethods:_test_method,_test_method2;"
            "tests.integration.test_tracemethods:_Class.test_method,_Class.test_method2;"
            "tests.integration.test_tracemethods:_Class.NestedClass.test_method"
        )
    ),
)
def test_ddtrace_run_trace_methods_sync():
    from tests.integration.test_tracemethods import _Class
    from tests.integration.test_tracemethods import _test_method
    from tests.integration.test_tracemethods import _test_method2

    _test_method()
    _test_method2()

    c = _Class()
    c.test_method()
    c.test_method2()

    n = _Class.NestedClass()
    n.test_method()


async def _async_test_method():
    await asyncio.sleep(0.01)


async def _async_test_method2():
    await asyncio.sleep(0.01)


@pytest.mark.snapshot()
def test_ddtrace_run_trace_methods_async(ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    env["DD_TRACE_METHODS"] = (
        "tests.integration.test_tracemethods:_async_test_method,_async_test_method2;"
        "tests.integration.test_tracemethods:_Class.async_test_method"
    )
    tests_dir = os.path.dirname(os.path.dirname(__file__))
    env["PYTHONPATH"] = os.pathsep.join([tests_dir, env.get("PYTHONPATH", "")])

    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import asyncio
from tests.integration.test_tracemethods import _async_test_method
from tests.integration.test_tracemethods import _async_test_method2
from tests.integration.test_tracemethods import _Class

async def main():
    await _async_test_method()
    await _async_test_method2()
    c = _Class()
    await c.async_test_method()
    await c.async_test_method2()

asyncio.run(main())
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""


@pytest.mark.snapshot()
def test_ddtrace_run_trace_methods_async_nested(ddtrace_run_python_code_in_subprocess):
    """
    This test ensures that async spans remain open for the duration of nested awaits
    in _Class._nested_async_test_method, which calls additional async functions.
    """
    import os

    env = os.environ.copy()
    env["DD_TRACE_METHODS"] = (
        "tests.integration.test_tracemethods:_Class._nested_async_test_method,"
        "tests.integration.test_tracemethods:_async_test_method,"
        "tests.integration.test_tracemethods:_async_test_method2"
    )

    tests_dir = os.path.dirname(os.path.dirname(__file__))
    env["PYTHONPATH"] = os.pathsep.join([tests_dir, env.get("PYTHONPATH", "")])

    code = r"""
import asyncio
from tests.integration.test_tracemethods import _Class

async def main():
    c = _Class()
    await c._nested_async_test_method()

asyncio.run(main())
"""

    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)

    assert status == 0, err
    assert out == b""
