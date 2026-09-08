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
