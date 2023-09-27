from importlib.machinery import ModuleSpec
from types import ModuleType

from ddtrace.internal.symbols import Scope
from ddtrace.internal.symbols import ScopeContext
from ddtrace.internal.symbols import ScopeType
from ddtrace.internal.symbols import Symbol
from ddtrace.internal.symbols import SymbolType


def test_symbol_from_code():
    def foo(a, b, c=None):
        loc = 42
        return loc

    symbols = Symbol.from_code(foo.__code__)
    assert {s.name for s in symbols if s.symbol_type == SymbolType.ARG} == {"a", "b", "c"}
    assert {s.name for s in symbols if s.symbol_type == SymbolType.LOCAL} == {"loc"}


def test_symbols_class():
    class Sym:
        def __init__(self):
            self._foo = "foo"

        @property
        def foo(self):
            return self._foo

        @foo.setter
        def _(self, value):
            self._foo = value

        @classmethod
        def bar(cls):
            pass

        @staticmethod
        def baz():
            pass

        def gen(n=10):
            yield from range(n)

        async def coro(b):
            oroc = 42
            yield oroc

    module = ModuleType("test")
    module.Sym = Sym
    module.__spec__ = ModuleSpec("test", None)
    module.__spec__.origin = __file__

    scope = Scope.from_module(module)

    (class_scope,) = scope.scopes
    assert class_scope.name == "Sym"

    (field,) = (s for s in class_scope.symbols if s.symbol_type == SymbolType.FIELD)
    assert field.name == "_foo"

    assert {s.name for s in class_scope.scopes if s.scope_type == ScopeType.FUNCTION} == {
        "test_symbols_class.<locals>.Sym.__init__",
        "test_symbols_class.<locals>.Sym.bar",
        "test_symbols_class.<locals>.Sym.baz",
        "test_symbols_class.<locals>.Sym.coro",
        "test_symbols_class.<locals>.Sym.foo",
        "test_symbols_class.<locals>.Sym.gen",
    }


def test_symbols_decorators():
    """Test that we get the undecorated functions from a module scope."""

    def deco(f):
        return f

    @deco
    def foo():
        pass

    module = ModuleType("test")
    module.foo = foo
    module.__spec__ = ModuleSpec("test", None)
    module.__spec__.origin = __file__

    scope = Scope.from_module(module)

    (foo_scope,) = scope.scopes
    assert foo_scope.name == "test_symbols_decorators.<locals>.foo"


def test_symbols_decorators_included():
    def deco(f):
        return f

    @deco
    def foo():
        pass

    module = ModuleType("test")
    module.deco = deco
    module.foo = foo
    module.__spec__ = ModuleSpec("test", None)
    module.__spec__.origin = __file__

    scope = Scope.from_module(module)

    assert {_.name for _ in scope.scopes} == {
        "test_symbols_decorators_included.<locals>.foo",
        "test_symbols_decorators_included.<locals>.deco",
    }


def test_symbols_decorated_methods():
    """Test that we get the undecorated class methods."""

    def method_decorator(f):
        def _(self, *args, **kwargs):
            return f(self, *args, **kwargs)

        return _

    class Foo:
        @method_decorator
        def bar(self):
            pass

    scope = Scope._get_from(Foo)
    (bar_scope,) = scope.scopes
    assert bar_scope.name == "test_symbols_decorated_methods.<locals>.Foo.bar"


def test_symbols_to_json():
    assert Scope(
        scope_type=ScopeType.MODULE,
        name="test",
        source_file=__file__,
        start_line=0,
        end_line=0,
        symbols=[
            Symbol(
                symbol_type=SymbolType.STATIC_FIELD,
                name="foo",
                line=0,
            ),
        ],
        scopes=[],
    ).to_json() == {
        "scope_type": ScopeType.MODULE,
        "name": "test",
        "source_file": __file__,
        "start_line": 0,
        "end_line": 0,
        "symbols": [
            {
                "symbol_type": SymbolType.STATIC_FIELD,
                "name": "foo",
                "line": 0,
            }
        ],
        "scopes": [],
    }


def test_symbols_scope_context():
    import ddtrace.debugging._debugger as d

    context = ScopeContext([Scope.from_module(d)])
    import json

    print(json.dumps(context.to_json(), indent=2))

    assert 200 <= context.upload().status < 300
