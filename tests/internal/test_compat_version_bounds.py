"""Python 3.15 wrapping: trampoline plus 3.15 generator/coroutine assemblies.

wrap() / wrap_bytecode() run on NEXT_MAX_PY and fail closed from NEXT_MAX_PY+1.
@lazy uses WrappingContext.wrap() (sys.monitoring) on 3.15+. inject_hook is
monitoring-based on 3.15+.
"""

# mypy: follow-imports=silent
from __future__ import annotations

import ast
import importlib
from importlib import resources
from pathlib import Path
import re
from types import CoroutineType
from types import FunctionType
from types import ModuleType
from typing import cast

import pytest

from ddtrace.internal.compat import MAX_PY
from ddtrace.internal.compat import NEXT_MAX_PY
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.compat import is_at_least_py
from ddtrace.internal.compat import is_at_most_py
from ddtrace.internal.compat import is_supported_python_version


# wrap() is live on 3.15+ while wrap is supported through NEXT_MAX_PY.
_WRAP_ON_315: bool = is_at_least_py(3, 15) and is_supported_python_version()

_FEATURE_GATE_MODULES: tuple[str, ...] = (
    "ddtrace/internal/wrapping/context.py",
    "ddtrace/internal/wrapping/asyncs.py",
    "ddtrace/internal/wrapping/generators.py",
    "ddtrace/internal/monitoring.py",
    "ddtrace/internal/bytecode_injection/__init__.py",
    "ddtrace/internal/coverage/instrumentation_py3_12.py",
    "ddtrace/internal/coverage/import_instrumentation_py3_12.py",
)

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_REQUIRES_PYTHON_UPPER: re.Pattern[str] = re.compile(
    r'^requires-python\s*=\s*"[^"]*<(\d+)\.(\d+)"',
    re.MULTILINE,
)


def _ddtrace_source(path: str) -> str:
    return resources.files("ddtrace").joinpath(path.removeprefix("ddtrace/")).read_text()


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
    assert is_at_most_py(*MAX_PY, version=MAX_PY)
    assert not is_at_most_py(*MAX_PY, version=NEXT_MAX_PY)
    assert not is_at_least_py(*NEXT_MAX_PY, version=MAX_PY)
    assert is_at_least_py(*NEXT_MAX_PY, version=NEXT_MAX_PY)
    assert is_supported_python_version(version=NEXT_MAX_PY)
    assert is_supported_python_version(version=(3, 15))
    assert not is_supported_python_version(version=(3, 16))
    fail_close: tuple[int, int] = (NEXT_MAX_PY[0], NEXT_MAX_PY[1] + 1)
    assert is_at_least_py(*NEXT_MAX_PY, version=fail_close)
    assert not is_supported_python_version(version=fail_close)
    running: tuple[int, ...] = PYTHON_VERSION_INFO[:2]
    assert is_at_least_py(*NEXT_MAX_PY) is is_at_least_py(*NEXT_MAX_PY, version=running)
    assert is_supported_python_version() is is_supported_python_version(version=running)
    assert not is_at_least_py(3, 15, version=(3, 14))
    assert is_at_least_py(3, 15, version=(3, 15))
    assert is_at_least_py(3, 15, version=(3, 16))
    assert is_at_least_py(3, 15) is is_at_least_py(3, 15, version=running)
    assert is_at_least_py(3, 10, version=(3, 10))
    assert not is_at_least_py(3, 10, version=(3, 9))
    assert is_at_most_py(3, 12, version=(3, 12))
    assert is_at_most_py(3, 12, version=(3, 11))
    assert not is_at_most_py(3, 12, version=(3, 13))
    assert is_at_most_py(3, 12) is is_at_most_py(3, 12, version=running)
    assert is_at_least_py(3, 11, version=(3, 12)) and is_at_most_py(3, 12, version=(3, 12))
    assert not (is_at_least_py(3, 11, version=(3, 13)) and is_at_most_py(3, 12, version=(3, 13)))


def _is_literal_major_minor_call(node: ast.Call) -> bool:
    if any(isinstance(arg, ast.Starred) for arg in node.args):
        return False
    if len(node.args) < 2:
        return False
    major: ast.expr = node.args[0]
    minor: ast.expr = node.args[1]
    return (
        isinstance(major, ast.Constant)
        and isinstance(major.value, int)
        and isinstance(minor, ast.Constant)
        and isinstance(minor.value, int)
    )


def _is_at_least_py_315_call(node: ast.Call) -> bool:
    func: ast.expr = node.func
    if not isinstance(func, ast.Name) or func.id != "is_at_least_py":
        return False
    if not _is_literal_major_minor_call(node):
        return False
    major: ast.expr = node.args[0]
    minor: ast.expr = node.args[1]
    return (
        isinstance(major, ast.Constant) and major.value == 3 and isinstance(minor, ast.Constant) and minor.value == 15
    )


def test_py315_feature_gate_does_not_follow_next_max() -> None:
    """Wrap/coverage/monitoring 3.15 gates pass (3, 15), not NEXT_MAX_PY."""
    deleted_wrappers: set[str] = {
        "is_at_least_next_max_py",
        "is_py_version_within_bounds",
        "is_wrap_supported",
    }
    for relpath in _FEATURE_GATE_MODULES:
        source: str = _ddtrace_source(relpath)
        tree: ast.Module = ast.parse(source)
        found_315_gate: bool = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in deleted_wrappers:
                pytest.fail(f"{relpath} still names deleted wrapper {node.id}")
            if isinstance(node, ast.alias) and node.name in deleted_wrappers:
                pytest.fail(f"{relpath} still imports deleted wrapper {node.name}")
            if isinstance(node, ast.Name) and node.id == "NEXT_MAX_PY":
                pytest.fail(f"{relpath} references NEXT_MAX_PY; use is_supported_python_version()")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("is_at_least_py", "is_at_most_py")
            ):
                if not _is_literal_major_minor_call(node):
                    pytest.fail(f"{relpath} calls {node.func.id} without literal major, minor")
                if _is_at_least_py_315_call(node):
                    found_315_gate = True
        assert found_315_gate, f"{relpath} must call is_at_least_py(3, 15)"


def test_next_max_py_shift_updates_wrap_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    """is_at_least_py(*NEXT_MAX_PY) and is_supported_python_version read NEXT_MAX_PY."""
    compat: ModuleType = importlib.import_module("ddtrace.internal.compat")

    monkeypatch.setattr(compat, "NEXT_MAX_PY", (3, 16))
    assert not compat.is_at_least_py(*compat.NEXT_MAX_PY, version=(3, 15))
    assert compat.is_at_least_py(*compat.NEXT_MAX_PY, version=(3, 16))
    assert compat.is_supported_python_version(version=(3, 15))
    assert compat.is_supported_python_version(version=(3, 16))
    assert not compat.is_supported_python_version(version=(3, 17))


def test_wrapping_modules_import() -> None:
    import ddtrace.internal.bytecode_injection  # noqa: F401
    import ddtrace.internal.module  # noqa: F401
    import ddtrace.internal.wrapping.asyncs  # noqa: F401
    import ddtrace.internal.wrapping.generators  # noqa: F401

    # wrapping.context fail-closes at import when wrap is unsupported (3.16+).
    if is_supported_python_version():
        import ddtrace.internal.wrapping.context  # noqa: F401
    else:
        with pytest.raises(NotImplementedError, match="not supported yet"):
            import ddtrace.internal.wrapping.context  # noqa: F401


@pytest.mark.skipif(not _WRAP_ON_315, reason="wrap() trampoline on 3.15")
def test_wrap_runs_on_315() -> None:
    from ddtrace.internal.wrapping import wrap

    seen: list[object] = []

    def wrapper(wrapped, args, kwargs):  # noqa: ANN001, ANN202
        seen.append("sync")
        return wrapped(*args, **kwargs)

    def f() -> int:
        return 7

    wrap(cast(FunctionType, f), wrapper)
    assert f() == 7
    assert seen == ["sync"]

    def gen_wrapper(wrapped, args, kwargs):  # noqa: ANN001, ANN202
        seen.append("gen")
        for value in wrapped(*args, **kwargs):
            yield value

    def g():  # noqa: ANN202
        yield 1
        yield 2

    wrap(cast(FunctionType, g), gen_wrapper)
    assert list(g()) == [1, 2]
    assert seen == ["sync", "gen"]


@pytest.mark.skipif(not _WRAP_ON_315, reason="wrap() coroutine on 3.15")
@pytest.mark.asyncio
async def test_wrap_coroutine_on_315() -> None:
    from ddtrace.internal.wrapping import wrap

    seen: list[object] = []

    def wrapper(wrapped, args, kwargs):  # noqa: ANN001, ANN202
        result: object = wrapped(*args, **kwargs)
        if isinstance(result, CoroutineType):

            async def _await(coro):  # noqa: ANN001, ANN202
                value: object = await coro
                seen.append(value)
                return value

            return _await(result)
        seen.append(result)
        return result

    async def c() -> int:
        return 42

    wrap(cast(FunctionType, c), wrapper)
    assert await c() == 42
    assert seen == [42]


def test_wrap_raises_not_implemented_on_future_py(monkeypatch: pytest.MonkeyPatch) -> None:
    """wrap() must fail closed from 3.16 on."""
    wrapping: ModuleType = importlib.import_module("ddtrace.internal.wrapping")

    # wrap()/wrap_bytecode resolve the module binding at call time.
    monkeypatch.setattr(wrapping, "is_supported_python_version", lambda *args, **kwargs: False)

    def f() -> None:
        return None

    def wrapper(wrapped, args, kwargs):  # noqa: ANN001, ANN202
        return wrapped(*args, **kwargs)

    with pytest.raises(NotImplementedError, match="not supported yet"):
        wrapping.wrap(f, wrapper)
    with pytest.raises(NotImplementedError, match="not supported yet"):
        wrapping.wrap_bytecode(wrapper, f)


@pytest.mark.skipif(not _WRAP_ON_315, reason="lazy module wrap on 3.15")
def test_lazy_module_decorator_without_bytecode_wrap() -> None:
    lazy_module: ModuleType = importlib.import_module("tests.internal.lazy")

    assert lazy_module.new_value == 42


def test_exec_lazy_init_without_source() -> None:
    from ddtrace.internal.lazy import _exec_lazy_init

    ns: dict[str, object] = {}
    exec(compile("def init():\n    exported = 123\n", "<test>", "exec"), ns)
    module_globals: dict[str, object] = {"__name__": "test_lazy_init"}
    _exec_lazy_init(cast(FunctionType, ns["init"]), module_globals)
    assert module_globals["exported"] == 123


@pytest.mark.skipif(not _WRAP_ON_315, reason="debugging products load on 3.15")
def test_debugging_products_load_without_failure() -> None:
    from ddtrace.internal.products import ProductManager

    product_manager: ProductManager = ProductManager()
    product_manager._load_products()
    for product_name in (
        "code-origin-for-spans",
        "dynamic-instrumentation",
        "exception-replay",
        "live-debugger",
    ):
        assert product_name not in product_manager._failed


@pytest.mark.skipif(not _WRAP_ON_315, reason="inject_hook on 3.15")
def test_inject_hook_does_not_raise_on_315() -> None:
    from ddtrace.internal.bytecode_injection import inject_hook
    from ddtrace.internal.utils.inspection import linenos

    def f() -> None:
        return None

    def hook(_arg: object) -> None:
        return None

    inject_hook(cast(FunctionType, f), hook, min(linenos(f)), None)
