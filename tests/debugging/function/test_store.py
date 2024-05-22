import mock
from mock.mock import call

from ddtrace.debugging._function.discovery import FunctionDiscovery
from ddtrace.debugging._function.discovery import undecorated
from ddtrace.debugging._function.store import FunctionStore
from ddtrace.internal.module import origin
from ddtrace.internal.utils.inspection import linenos
from ddtrace.internal.wrapping.context import WrappingContext
import tests.submod.stuff as stuff


class MockWrappingContext(WrappingContext):
    def __init__(self, f, mock, arg):
        super().__init__(f)

        self.mock = mock
        self.arg = arg

    def __exit__(self, *exc):
        pass

    def __return__(self, value):
        self.mock(self.arg)
        return value


class MockProbe:
    def __init__(self, probe_id):
        self.probe_id = probe_id


def test_function_inject():
    with FunctionStore() as store:
        lo = min(linenos(stuff.modulestuff))
        function = FunctionDiscovery.from_module(stuff).at_line(lo)[0]
        hook = mock.Mock()()

        store.inject_hook(function, hook, lo, 42)
        stuff.modulestuff(None)
        hook.assert_called_once_with(42)

    stuff.modulestuff(None)
    hook.assert_called_once_with(42)


def test_function_wrap():
    with FunctionStore() as store:
        function = FunctionDiscovery.from_module(stuff).by_name(stuff.modulestuff.__name__)

        assert function is stuff.modulestuff

        arg = mock.Mock()
        store.wrap(function, MockWrappingContext(function, arg, 42))

        stuff.modulestuff(None)

        arg.assert_called_once_with(42)

    stuff.modulestuff(None)
    arg.assert_called_once_with(42)


def test_function_inject_wrap():
    with FunctionStore() as store:
        # Function retrieval
        lo = min(linenos(stuff.modulestuff))
        function = FunctionDiscovery.from_module(stuff).at_line(lo)[0]
        assert function is FunctionDiscovery.from_module(stuff).by_name(function.__name__)
        assert function is stuff.modulestuff

        # Injection
        hook = mock.Mock()()
        store.inject_hook(function, hook, lo, 42)
        stuff.modulestuff(None)
        hook.assert_called_once_with(42)

        # Wrapping
        arg = mock.Mock()
        store.wrap(stuff.modulestuff, MockWrappingContext(stuff.modulestuff, arg, 42))
        stuff.modulestuff(None)
        arg.assert_called_once_with(42)

        # Restore
        store.restore_all()
        stuff.modulestuff(None)
        hook.assert_has_calls([call(42), call(42)])
        arg.assert_called_once_with(42)


def test_function_wrap_inject():
    with FunctionStore() as store:
        # Function retrieval
        lo = min(linenos(stuff.modulestuff))
        function = FunctionDiscovery.from_module(stuff).by_name(stuff.modulestuff.__name__)
        assert function is FunctionDiscovery.from_module(stuff).at_line(lo)[0]

        # Wrapping
        arg = mock.Mock()
        store.wrap(function, MockWrappingContext(function, arg, 42))
        stuff.modulestuff(None)
        arg.assert_called_once_with(42)

        # Injection
        hook = mock.Mock()()
        store.inject_hook(function, hook, lo, 42)
        stuff.modulestuff(None)
        hook.assert_called_once_with(42)

        # Restore
        store.restore_all()
        stuff.modulestuff(None)
        hook.assert_called_once_with(42)
        arg.assert_has_calls([call(42), call(42)])


def test_function_wrap_property():
    with FunctionStore() as store:
        function = FunctionDiscovery.from_module(stuff).by_name(
            ".".join((stuff.Stuff.__name__, stuff.Stuff.propertystuff.fget.__name__, "getter"))
        )

        assert function is stuff.Stuff.propertystuff.fget

        arg = mock.Mock()
        store.wrap(function, MockWrappingContext(function, arg, 42))

        s = stuff.Stuff()
        s.propertystuff

        arg.assert_called_once_with(42)

        store.restore_all()
        s.propertystuff
        arg.assert_called_once_with(42)


def test_function_wrap_decorated():
    with FunctionStore() as store:
        function = FunctionDiscovery.from_module(stuff).by_name(
            ".".join((stuff.Stuff.__name__, "doublydecoratedstuff"))
        )

        assert function is not stuff.Stuff.doublydecoratedstuff
        assert function is undecorated(stuff.Stuff.doublydecoratedstuff, "doublydecoratedstuff", origin(stuff))

        arg = mock.Mock()
        store.wrap(function, MockWrappingContext(function, arg, 42))

        s = stuff.Stuff()
        s.doublydecoratedstuff()

        arg.assert_called_once_with(42)

        store.restore_all()
        s.doublydecoratedstuff()
        arg.assert_called_once_with(42)


def test_function_unwrap():
    with FunctionStore() as store:
        function = FunctionDiscovery.from_module(stuff).by_name(stuff.modulestuff.__name__)
        assert function is stuff.modulestuff

        store.wrap(function, MockWrappingContext(function, None, 42))

        store.unwrap(stuff.modulestuff)


def test_function_inject_wrap_commutativity():
    with FunctionStore() as store:
        code = stuff.modulestuff.__code__

        # Injection
        lo = min(linenos(stuff.modulestuff))
        function = FunctionDiscovery.from_module(stuff).at_line(lo)[0]
        hook = mock.Mock()()
        store.inject_hook(function, hook, lo, 42)

        # Wrapping
        function = FunctionDiscovery.from_module(stuff).by_name(stuff.modulestuff.__name__)
        assert function.__code__ is stuff.modulestuff.__code__
        store.wrap(function, MockWrappingContext(function, None, 42))

        # Ejection
        assert stuff.modulestuff.__code__ is not code
        store.eject_hook(stuff.modulestuff, hook, lo, 42)

        # Unwrapping
        store.unwrap(stuff.modulestuff)


def test_function_wrap_inject_commutativity():
    with FunctionStore() as store:
        # Wrapping
        function = FunctionDiscovery.from_module(stuff).by_name(stuff.modulestuff.__name__)
        assert function is stuff.modulestuff
        lo = min(linenos(function))
        store.wrap(function, MockWrappingContext(function, None, 42))

        # Injection
        function = FunctionDiscovery.from_module(stuff).at_line(lo)[0]
        hook = mock.Mock()()
        probe = MockProbe(42)
        store.inject_hook(function, hook, lo, probe)

        # Unwrapping
        store.unwrap(stuff.modulestuff)

        # Ejection
        store.eject_hook(stuff.modulestuff, hook, lo, probe)
