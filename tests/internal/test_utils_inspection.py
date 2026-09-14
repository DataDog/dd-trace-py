from functools import wraps
from pathlib import Path

import pytest

from ddtrace.internal.utils.inspection import undecorated


def test_undecorated():
    def d(f):
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)

        return wrapper

    def f():
        pass

    df = d(f)
    assert df is not f

    ddf = d(df)
    assert ddf is not df

    dddf = d(ddf)
    assert dddf is not ddf

    name, path = f.__code__.co_name, Path(__file__).resolve()
    assert f is undecorated(dddf, name, path)
    assert f is undecorated(ddf, name, path)
    assert f is undecorated(df, name, path)
    assert f is undecorated(f, name, path)

    assert undecorated(undecorated, name, path) is undecorated


def test_undecorated_plain_function():
    # A plain, undecorated function is the common case: the pytest plugin resolves one per
    # test to report its source lines. It must come back unchanged, and a name that matches
    # nothing must still fall back to the object it was given.
    def f():
        pass

    path = Path(__file__).resolve()

    assert undecorated(f, f.__code__.co_name, path) is f
    assert undecorated(f, "does_not_exist", path) is f


def test_undecorated_methods():
    # Bound methods reach undecorated() from the pytest plugin, for class-based tests. They
    # resolve through the last-resort __dir__() scan, which the fast path deliberately does
    # not short-circuit; these assertions pin that behaviour so it cannot drift.
    def d(f):
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)

        return wrapper

    class C:
        def plain(self):
            pass

        @d
        def decorated(self):
            pass

    path = Path(__file__).resolve()
    instance = C()

    # An undecorated method resolves to the function behind the bound method.
    assert undecorated(instance.plain, "plain", path) is C.plain
    assert undecorated(C.plain, "plain", path) is C.plain

    # A name that matches nothing reachable falls back to the object it was given.
    # Bind once: attribute access builds a new bound method object each time.
    plain = instance.plain
    assert undecorated(plain, "nonexistent", path) is plain

    # Decorated *methods* are a long-standing gap: the closure walk only runs for plain
    # functions, so a bound method wrapping a closure-based decorator resolves to the
    # wrapper rather than the decorated body.
    decorated = instance.decorated
    assert undecorated(decorated, "decorated", path) is decorated


def test_undecorated_same_name_wrapper_returns_original():
    # Regression test for the precedence the self-match shortcut must not disturb. A
    # decorator's wrapper may legitimately share the target's name and source file, which
    # makes it match() the target. The explicit wrapper/closure probes must still run
    # first, so the original -- not the wrapper -- is returned.
    def decorate(original):
        def test_target():
            return original()

        return test_target

    def test_target():
        pass

    original = test_target
    wrapper = decorate(original)

    assert wrapper.__code__.co_name == original.__code__.co_name
    assert undecorated(wrapper, name="test_target", path=Path(__file__).resolve()) is original


def test_undecorated_same_name_outer_wrapper_defers_to_queued_candidates():
    # As above, but with a non-matching wrapper in between. The outer wrapper matches on
    # name and file, so a self-match shortcut must not fire while the intermediate is still
    # queued: the breadth-first search reaches it on the next iteration and finds the real
    # original through its closure.
    def outer_decorator(fn):
        def test_target():  # matches the target name
            return fn()

        return test_target

    def intermediate_decorator(fn):
        def wrapper():  # does not match
            return fn()

        return wrapper

    def test_target():
        pass

    original = test_target
    intermediate = intermediate_decorator(original)
    outer = outer_decorator(intermediate)

    assert outer.__code__.co_name == original.__code__.co_name
    assert intermediate.__code__.co_name != original.__code__.co_name
    assert undecorated(outer, name="test_target", path=Path(__file__).resolve()) is original


def test_class_decoration():
    class Decorator:
        def __init__(self, f):
            self.f = f

    @Decorator
    def f():
        pass

    code = undecorated(f, name="f", path=Path(__file__).resolve()).__code__
    assert code.co_name == "f"
    assert Path(code.co_filename).resolve() == Path(__file__).resolve()


def test_wrapped_decoration():
    @wraps
    def f():
        pass

    code = undecorated(f, name="f", path=Path(__file__).resolve()).__code__
    assert code.co_name == "f"
    assert Path(code.co_filename).resolve() == Path(__file__).resolve()


@pytest.mark.subprocess
def test_module_code_collector_finds_decorator_discarded_code():
    # tests.submod.custom_decorated_stuff's "home" function is rebound to None
    # by its decorator, so it is unreachable via a namespace walk. The
    # collector still finds it because it collects code objects at compile
    # time, before the decorator runs.
    from ddtrace.internal.utils.inspection import ModuleCodeCollector

    ModuleCodeCollector.register("test")

    import tests.submod.custom_decorated_stuff as custom_decorated_stuff

    assert custom_decorated_stuff.home is None

    code_objects = ModuleCodeCollector.get_code_objects(custom_decorated_stuff)
    assert code_objects is not None
    assert any(c.co_name == "home" for c in code_objects)


@pytest.mark.subprocess
def test_module_code_collector_returns_none_without_registration():
    from ddtrace.internal.utils.inspection import ModuleCodeCollector
    import tests.submod.custom_decorated_stuff as custom_decorated_stuff

    assert ModuleCodeCollector.get_code_objects(custom_decorated_stuff) is None


@pytest.mark.subprocess
def test_module_code_collector_keeps_entry_until_every_subscriber_releases():
    from ddtrace.internal.utils.inspection import ModuleCodeCollector

    ModuleCodeCollector.register("a")
    ModuleCodeCollector.register("b")

    import tests.submod.custom_decorated_stuff as custom_decorated_stuff

    assert ModuleCodeCollector.get_code_objects(custom_decorated_stuff) is not None
    assert ModuleCodeCollector.get_code_objects(custom_decorated_stuff) is not None

    ModuleCodeCollector.release(custom_decorated_stuff, "a")

    # "b" has not released yet, so the entry, and its data, are still there.
    assert ModuleCodeCollector.get_code_objects(custom_decorated_stuff) is not None
    assert custom_decorated_stuff in ModuleCodeCollector._instance._code

    ModuleCodeCollector.release(custom_decorated_stuff, "b")

    # Every registered subscriber has released it, so the entry is dropped.
    assert custom_decorated_stuff not in ModuleCodeCollector._instance._code


@pytest.mark.subprocess
def test_module_code_collector_late_subscriber_is_not_pending():
    # A subscriber that registers after a module was compiled was not part of
    # that module's pending snapshot. It can still read the module's data, but
    # releasing on its behalf does not count towards eviction.
    from ddtrace.internal.utils.inspection import ModuleCodeCollector

    ModuleCodeCollector.register("a")

    import tests.submod.custom_decorated_stuff as custom_decorated_stuff

    ModuleCodeCollector.register("b")

    assert ModuleCodeCollector.get_code_objects(custom_decorated_stuff) is not None

    ModuleCodeCollector.release(custom_decorated_stuff, "b")
    assert custom_decorated_stuff in ModuleCodeCollector._instance._code

    ModuleCodeCollector.release(custom_decorated_stuff, "a")
    assert custom_decorated_stuff not in ModuleCodeCollector._instance._code
