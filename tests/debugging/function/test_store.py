import sys

import mock
from mock.mock import call
import pytest
from six import PY2

from ddtrace.debugging._function.discovery import FunctionDiscovery
from ddtrace.debugging._function.store import FunctionStore
from ddtrace.internal.utils.inspection import linenos
import tests.submod.stuff as stuff


def gen_wrapper(*arg):
    def _wrapper(wrapped, args, kwargs):
        # take a snapshot of args and kwargs before they are modified by the
        # wrapped function
        try:
            result = wrapped(*args, **kwargs)
            # capture the return value
            mock, v = arg
            mock(v)
            return result
        except Exception:
            # capture the exception
            raise
        finally:
            # finalise and push the snapshot
            pass

    return _wrapper


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
        store.wrap(function, gen_wrapper(arg, 42))

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
        store.wrap(stuff.modulestuff, gen_wrapper(arg, 42))
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
        store.wrap(function, gen_wrapper(arg, 42))
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
        store.wrap(function, gen_wrapper(arg, 42))

        s = stuff.Stuff()
        s.propertystuff

        arg.assert_called_once_with(42)

        store.restore_all()
        s.propertystuff
        arg.assert_called_once_with(42)


@pytest.mark.xfail(
    sys.version_info < (3, 6),
    reason="Support for decorated methods is currently not available for this version of Python",
)
def test_function_wrap_decorated():
    with FunctionStore() as store:
        function = FunctionDiscovery.from_module(stuff).by_name(
            ".".join((stuff.Stuff.__name__, stuff.Stuff.doublydecoratedstuff.__name__))
        )

        if PY2:
            assert function.__code__ is stuff.Stuff.doublydecoratedstuff.__func__.__code__
        else:
            assert function is stuff.Stuff.doublydecoratedstuff

        arg = mock.Mock()
        store.wrap(function, gen_wrapper(arg, 42))

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
        code = function.__code__

        store.wrap(function, gen_wrapper(None, 42))
        assert code is not stuff.modulestuff.__code__

        store.unwrap(stuff.modulestuff)
        assert code is stuff.modulestuff.__code__


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
        store.wrap(function, gen_wrapper(None, 42))
        assert code is not stuff.modulestuff.__code__

        # Ejection
        assert stuff.modulestuff.__code__ is not code
        store.eject_hook(stuff.modulestuff, hook, lo, 42)

        # Unwrapping
        store.unwrap(stuff.modulestuff)

        assert stuff.modulestuff.__code__ is not code and stuff.modulestuff.__code__ == code


def test_function_wrap_inject_commutativity():
    with FunctionStore() as store:
        # Wrapping
        function = FunctionDiscovery.from_module(stuff).by_name(stuff.modulestuff.__name__)
        assert function is stuff.modulestuff
        code = function.__code__
        store.wrap(function, gen_wrapper(None, 42))
        assert code is not stuff.modulestuff.__code__

        # Injection
        lo = min(linenos(stuff.modulestuff.__dd_wrapped__))
        function = FunctionDiscovery.from_module(stuff).at_line(lo)[0]
        hook = mock.Mock()()
        store.inject_hook(function, hook, lo, 42)

        # Unwrapping
        store.unwrap(stuff.modulestuff)

        # Ejection
        assert stuff.modulestuff.__code__ is not code
        store.eject_hook(stuff.modulestuff, hook, lo, 42)

        assert stuff.modulestuff.__code__ is not code and stuff.modulestuff.__code__ == code
