"""Python 3.15 import-time degrade: wrapping modules must load.

wrap() runs the existing trampoline plus the 3.15 generator/coroutine
assemblies, and fails closed on the next CPython. Remaining NEXT_PY_VERSION
gates (lazy wrapping) still degrade instead of crashing import. inject_hook
is monitoring-based on 3.15.
"""

from types import CoroutineType

import pytest

from ddtrace.internal.compat import NEXT_PY_VERSION
from ddtrace.internal.compat import NEXT_PY_VERSION_INFO
from ddtrace.internal.compat import PYTHON_VERSION_INFO


def test_wrapping_modules_import():
    import ddtrace.internal.bytecode_injection  # noqa: F401
    import ddtrace.internal.module  # noqa: F401
    import ddtrace.internal.wrapping.asyncs  # noqa: F401
    import ddtrace.internal.wrapping.context  # noqa: F401
    import ddtrace.internal.wrapping.generators  # noqa: F401


@pytest.mark.skipif(PYTHON_VERSION_INFO < NEXT_PY_VERSION_INFO, reason=f"{NEXT_PY_VERSION} wrap() trampoline")
def test_wrap_runs_on_315():
    from ddtrace.internal.wrapping import wrap

    seen: list[object] = []

    def wrapper(wrapped, args, kwargs):  # noqa: ANN001, ANN202
        seen.append("sync")
        return wrapped(*args, **kwargs)

    def f() -> int:
        return 7

    wrap(f, wrapper)
    assert f() == 7
    assert seen == ["sync"]

    def gen_wrapper(wrapped, args, kwargs):  # noqa: ANN001, ANN202
        for value in wrapped(*args, **kwargs):
            yield value

    def g():  # noqa: ANN202
        yield 1
        yield 2

    wrap(g, gen_wrapper)
    assert list(g()) == [1, 2]


@pytest.mark.skipif(PYTHON_VERSION_INFO < NEXT_PY_VERSION_INFO, reason=f"{NEXT_PY_VERSION} wrap() coroutine")
@pytest.mark.asyncio
async def test_wrap_coroutine_on_315():
    from ddtrace.internal.wrapping import wrap

    seen: list[object] = []

    def wrapper(wrapped, args, kwargs):  # noqa: ANN001, ANN202
        result = wrapped(*args, **kwargs)
        if isinstance(result, CoroutineType):

            async def _await(coro):  # noqa: ANN001, ANN202
                value = await coro
                seen.append(value)
                return value

            return _await(result)
        seen.append(result)
        return result

    async def c() -> int:
        return 42

    wrap(c, wrapper)
    assert await c() == 42
    assert seen == [42]


def test_wrap_raises_not_implemented_on_future_py(monkeypatch):
    """wrap() must fail closed on the CPython after NEXT_PY_VERSION."""
    import ddtrace.internal.wrapping as wrapping

    monkeypatch.setattr(
        wrapping,
        "PY",
        (NEXT_PY_VERSION_INFO[0], NEXT_PY_VERSION_INFO[1] + 1),
    )

    def f() -> None:
        return None

    def wrapper(wrapped, args, kwargs):  # noqa: ANN001, ANN202
        return wrapped(*args, **kwargs)

    with pytest.raises(NotImplementedError, match="not supported yet"):
        wrapping.wrap(f, wrapper)
    with pytest.raises(NotImplementedError, match="not supported yet"):
        wrapping.wrap_bytecode(wrapper, f)


@pytest.mark.skipif(PYTHON_VERSION_INFO < NEXT_PY_VERSION_INFO, reason=f"{NEXT_PY_VERSION} lazy module degrade")
def test_lazy_module_decorator_without_bytecode_wrap():
    import tests.internal.lazy as lazy_module

    assert lazy_module.new_value == 42


def test_exec_lazy_init_without_source():
    from ddtrace.internal.module import _exec_lazy_init

    ns: dict[str, object] = {}
    exec(compile("def init():\n    exported = 123\n", "<test>", "exec"), ns)
    module_globals: dict[str, object] = {"__name__": "test_lazy_init"}
    _exec_lazy_init(ns["init"], module_globals)
    assert module_globals["exported"] == 123


@pytest.mark.skipif(PYTHON_VERSION_INFO < NEXT_PY_VERSION_INFO, reason=f"{NEXT_PY_VERSION} debugging products degrade")
def test_debugging_products_load_without_failure():
    from ddtrace.internal.products import ProductManager

    product_manager = ProductManager()
    product_manager._load_products()
    for product_name in (
        "code-origin-for-spans",
        "dynamic-instrumentation",
        "exception-replay",
        "live-debugger",
    ):
        assert product_name not in product_manager._failed


@pytest.mark.skipif(PYTHON_VERSION_INFO < NEXT_PY_VERSION_INFO, reason=f"{NEXT_PY_VERSION} inject_hook")
def test_inject_hook_does_not_raise_on_315():
    from ddtrace.internal.bytecode_injection import inject_hook
    from ddtrace.internal.utils.inspection import linenos

    def f() -> None:
        return None

    def hook(_arg: object) -> None:
        return None

    inject_hook(f, hook, min(linenos(f)), None)
