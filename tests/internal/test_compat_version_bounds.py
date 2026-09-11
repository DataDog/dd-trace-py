"""Python 3.15 wrapping: trampoline plus 3.15 generator/coroutine assemblies.

wrap() / wrap_bytecode() run on NEXT_MAX_PY and fail closed from NEXT_MAX_PY+1.
@lazy uses WrappingContext.wrap() (sys.monitoring) on NEXT_MAX_PY. inject_hook is
monitoring-based on NEXT_MAX_PY.
"""

# mypy: follow-imports=silent
from __future__ import annotations

import ast
from pathlib import Path
import re
from types import CoroutineType

import pytest

from ddtrace.internal.compat import MAX_PY
from ddtrace.internal.compat import NEXT_MAX_PY
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.compat import is_at_least_next_max_py
from ddtrace.internal.compat import is_py_version_within_bounds
from ddtrace.internal.compat import is_wrap_supported


# wrap() is live on NEXT_MAX_PY while is_wrap_supported().
_WRAP_ON_NEXT_MAX: bool = is_at_least_next_max_py() and is_wrap_supported()

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_REQUIRES_PYTHON_UPPER: re.Pattern[str] = re.compile(
    r'^requires-python\s*=\s*"[^"]*<(\d+)\.(\d+)"',
    re.MULTILINE,
)


def _riotfile_simple_str_assignment(source: str, name: str) -> str | None:
    """Parse a module-level string assignment without importing riotfile."""
    tree: ast.Module = ast.parse(source)
    for node in tree.body:
        value: ast.expr | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            value = node.value
        if value is not None and isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def test_max_py_matches_requires_python_upper_bound() -> None:
    pyproject: str = (_REPO_ROOT / "pyproject.toml").read_text()
    match: re.Match[str] | None = _REQUIRES_PYTHON_UPPER.search(pyproject)
    assert match is not None, "pyproject.toml requires-python must have an exclusive <X.Y upper bound"
    major: int = int(match.group(1))
    minor: int = int(match.group(2))
    last_supported: tuple[int, int] = (major, minor - 1)
    assert MAX_PY == last_supported
    riotfile: str = (_REPO_ROOT / "riotfile.py").read_text()
    next_python_version: str | None = _riotfile_simple_str_assignment(riotfile, "NEXT_PYTHON_VERSION")
    assert next_python_version == f"{NEXT_MAX_PY[0]}.{NEXT_MAX_PY[1]}"
    max_python_version: str | None = _riotfile_simple_str_assignment(riotfile, "MAX_PYTHON_VERSION")
    assert max_python_version == f"{MAX_PY[0]}.{MAX_PY[1]}"


def test_version_bound_helpers() -> None:
    assert is_py_version_within_bounds(MAX_PY)
    assert not is_py_version_within_bounds(NEXT_MAX_PY)
    assert not is_at_least_next_max_py(MAX_PY)
    assert is_at_least_next_max_py(NEXT_MAX_PY)
    assert is_wrap_supported(NEXT_MAX_PY)
    fail_close: tuple[int, int] = (NEXT_MAX_PY[0], NEXT_MAX_PY[1] + 1)
    assert is_at_least_next_max_py(fail_close)
    assert not is_wrap_supported(fail_close)
    running: tuple[int, ...] = PYTHON_VERSION_INFO[:2]
    assert is_at_least_next_max_py() is is_at_least_next_max_py(running)


def test_wrapping_modules_import():
    import ddtrace.internal.bytecode_injection  # noqa: F401
    import ddtrace.internal.module  # noqa: F401
    import ddtrace.internal.wrapping.asyncs  # noqa: F401
    import ddtrace.internal.wrapping.context  # noqa: F401
    import ddtrace.internal.wrapping.generators  # noqa: F401


@pytest.mark.skipif(not _WRAP_ON_NEXT_MAX, reason="wrap() trampoline on 3.15")
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
        seen.append("gen")
        for value in wrapped(*args, **kwargs):
            yield value

    def g():  # noqa: ANN202
        yield 1
        yield 2

    wrap(g, gen_wrapper)
    assert list(g()) == [1, 2]
    assert seen == ["sync", "gen"]


@pytest.mark.skipif(not _WRAP_ON_NEXT_MAX, reason="wrap() coroutine on 3.15")
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
    """wrap() must fail closed from 3.16 on."""
    import ddtrace.internal.wrapping as wrapping

    fail_close: tuple[int, int] = (NEXT_MAX_PY[0], NEXT_MAX_PY[1] + 1)
    monkeypatch.setattr(wrapping, "PY", fail_close)

    def f() -> None:
        return None

    def wrapper(wrapped, args, kwargs):  # noqa: ANN001, ANN202
        return wrapped(*args, **kwargs)

    with pytest.raises(NotImplementedError, match="not supported yet"):
        wrapping.wrap(f, wrapper)
    with pytest.raises(NotImplementedError, match="not supported yet"):
        wrapping.wrap_bytecode(wrapper, f)


@pytest.mark.skipif(not _WRAP_ON_NEXT_MAX, reason="lazy module wrap on 3.15")
def test_lazy_module_decorator_without_bytecode_wrap():
    import tests.internal.lazy as lazy_module

    assert lazy_module.new_value == 42


def test_exec_lazy_init_without_source():
    from ddtrace.internal.lazy import _exec_lazy_init

    ns: dict[str, object] = {}
    exec(compile("def init():\n    exported = 123\n", "<test>", "exec"), ns)
    module_globals: dict[str, object] = {"__name__": "test_lazy_init"}
    _exec_lazy_init(ns["init"], module_globals)
    assert module_globals["exported"] == 123


@pytest.mark.skipif(not _WRAP_ON_NEXT_MAX, reason="debugging products load on 3.15")
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


@pytest.mark.skipif(not _WRAP_ON_NEXT_MAX, reason="inject_hook on 3.15")
def test_inject_hook_does_not_raise_on_315():
    from ddtrace.internal.bytecode_injection import inject_hook
    from ddtrace.internal.utils.inspection import linenos

    def f() -> None:
        return None

    def hook(_arg: object) -> None:
        return None

    inject_hook(f, hook, min(linenos(f)), None)
