from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
import typing as t

import pytest

from ddtrace.internal.symbol_db.symbols import Scope
from ddtrace.internal.symbol_db.symbols import ScopeData
from ddtrace.internal.symbol_db.symbols import ScopeType
from ddtrace.internal.symbol_db.symbols import Symbol
from ddtrace.internal.symbol_db.symbols import SymbolType


def test_symbol_from_code():
    def foo(a, b, c=None):
        loc = 42
        return loc

    symbols = Symbol.from_code(foo.__code__)
    assert {s.name for s in symbols if s.symbol_type == SymbolType.ARG} == {"a", "b", "c"}
    assert {s.name for s in symbols if s.symbol_type == SymbolType.LOCAL} == {"loc"}


def test_symbols_class():
    class Sup:
        pass

    class Sym(Sup):
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

        def gen(n: int = 10, _untyped=None) -> t.Generator[int, None, None]:
            yield from range(n)

        async def coro(b):
            oroc = 42
            yield oroc

        def me(self) -> "Sym":
            return self

    module = ModuleType("test")
    module.Sym = Sym
    module.__spec__ = ModuleSpec("test", None)
    module.__spec__.origin = __file__

    scope = Scope.from_module(module)

    (class_scope,) = scope.scopes
    assert class_scope.name == "tests.internal.symbol_db.test_symbols.test_symbols_class.<locals>.Sym"

    assert class_scope.language_specifics == {
        "super_classes": ["tests.internal.symbol_db.test_symbols.test_symbols_class.<locals>.Sup"]
    }

    (field,) = (s for s in class_scope.symbols if s.symbol_type == SymbolType.FIELD)
    assert field.name == "_foo"

    assert {s.name for s in class_scope.scopes if s.scope_type == ScopeType.FUNCTION} == {
        "__init__",
        "bar",
        "baz",
        "coro",
        "foo",
        "gen",
        "me",
    }

    gen_scope = next(_ for _ in class_scope.scopes if _.name == "gen")
    assert gen_scope.language_specifics == {
        "return_type": "typing.Generator[int, NoneType, NoneType]",
        "function_type": "generator",
    }
    gen_line = Sym.gen.__code__.co_firstlineno + 1
    assert gen_scope.symbols == [
        Symbol(symbol_type=SymbolType.ARG, name="n", line=gen_line, type="int"),
        Symbol(symbol_type=SymbolType.ARG, name="_untyped", line=gen_line, type=None),
    ]

    assert next(_ for _ in class_scope.scopes if _.name == "foo").language_specifics == {"method_type": "property"}

    assert next(_ for _ in class_scope.scopes if _.name == "bar").language_specifics == {"method_type": "class"}

    assert next(_ for _ in class_scope.scopes if _.name == "me").language_specifics == {"return_type": "Sym"}


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
    assert foo_scope.name == "foo"


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

    assert {_.name for _ in scope.scopes} == {"foo", "deco"}


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

    scope = Scope._get_from(Foo, ScopeData(Path(__file__), set()))
    (bar_scope,) = scope.scopes
    assert bar_scope.name == "bar"


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
                "type": None,
            }
        ],
        "scopes": [],
        "language_specifics": {},
    }


@pytest.mark.subprocess(ddtrace_run=True, env=dict(DD_SYMBOL_DATABASE_UPLOAD_ENABLED="1"))
def test_symbols_upload_enabled():
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
    from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

    assert not SymbolDatabaseUploader.is_installed()
    assert remoteconfig_poller.get_registered("LIVE_DEBUGGING_SYMBOL_DB") is not None


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_SYMBOL_DATABASE_UPLOAD_ENABLED="1",
        _DD_SYMBOL_DATABASE_FORCE_UPLOAD="1",
        DD_SYMBOL_DATABASE_INCLUDES="tests.submod.stuff",
    ),
)
def test_symbols_force_upload():
    from ddtrace.internal.symbol_db.symbols import ScopeType
    from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

    assert SymbolDatabaseUploader.is_installed()

    contexts = []

    def _upload_context(context):
        contexts.append(context)

    SymbolDatabaseUploader._upload_context = staticmethod(_upload_context)

    import tests.submod.stuff  # noqa
    import tests.submod.traced_stuff  # noqa

    (context,) = contexts

    (scope,) = context.to_json()["scopes"]
    assert scope["scope_type"] == ScopeType.MODULE
    assert scope["name"] == "tests.submod.stuff"
