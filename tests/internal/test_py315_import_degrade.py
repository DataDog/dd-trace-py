"""Python 3.15 import-time degrade: wrapping must load, wrap() still raises.

Until #17849 lands bytecode wrapping for 3.15, products that import wrapping
(e.g. ModuleWatchdog) must not crash the process. wrap()/inject_hook still
raise NotImplementedError when actually used.
"""

import sys

import pytest


def test_wrapping_modules_import():
    import ddtrace.internal.bytecode_injection  # noqa: F401
    import ddtrace.internal.module  # noqa: F401
    import ddtrace.internal.wrapping.asyncs  # noqa: F401
    import ddtrace.internal.wrapping.context  # noqa: F401
    import ddtrace.internal.wrapping.generators  # noqa: F401


@pytest.mark.skipif(sys.version_info < (3, 15), reason="3.15 wrap() degrade")
def test_wrap_raises_not_implemented_on_315():
    from ddtrace.internal.wrapping import wrap

    def f() -> None:
        return None

    def wrapper(wrapped, args, kwargs):  # noqa: ANN001, ANN202
        return wrapped(*args, **kwargs)

    with pytest.raises(NotImplementedError, match="3.15"):
        wrap(f, wrapper)


@pytest.mark.skipif(sys.version_info < (3, 15), reason="3.15 inject_hook degrade")
def test_inject_hook_raises_not_implemented_on_315():
    from ddtrace.internal.bytecode_injection import inject_hook

    def f() -> None:
        return None

    def hook(_arg: object) -> None:
        return None

    with pytest.raises(NotImplementedError, match="3.15"):
        inject_hook(f, hook, f.__code__.co_firstlineno, None)
