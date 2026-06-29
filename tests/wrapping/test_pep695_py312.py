"""PEP 695 generics in wrapped functions, methods, and classes — Python 3.12+.

Collected only on 3.12+ (see conftest.py). Generic callables carry
``__type_params__`` and a synthetic type-parameter scope; the trampolines rebuild
the code object and must keep both intact.
"""

from tests.wrapping._harness import run


def test_generic_function(mech):
    def f[T](x: T) -> T:
        return x

    assert mech.wrap_function(f)(7) == 7


def test_generic_function_type_params_preserved(mech):
    def f[T](x: T) -> T:
        return x

    g = mech.wrap_function(f)
    assert [tp.__name__ for tp in g.__type_params__] == ["T"]


def test_generic_function_uses_type_param(mech):
    def f[T](x: T):
        return (x, T.__name__)

    assert mech.wrap_function(f)(7) == (7, "T")


def test_generic_method(mech):
    class C:
        def m[U](self, x: U) -> U:
            return x

    mech.install_method(C, "m", "instance_method")
    assert C().m(7) == 7


def test_method_on_generic_class(mech):
    class C[T]:
        def m(self, x):
            return x

    mech.install_method(C, "m", "instance_method")
    assert C().m(7) == 7


def test_generic_coroutine(mech):
    import asyncio

    async def c[T](x: T) -> T:
        await asyncio.sleep(0)
        return x

    assert run(mech.wrap_function(c)(7)) == 7
