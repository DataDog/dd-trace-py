from os.path import abspath

import pytest

from ddtrace.debugging._function.discovery import FunctionDiscovery
from ddtrace.debugging._function.discovery import _undecorate
from ddtrace.internal.compat import PY2
import tests.submod.stuff as stuff


def _f(f):
    if PY2:
        return f.im_func
    return f


@pytest.fixture
def stuff_discovery():
    return FunctionDiscovery.from_module(stuff)


def test_abs_stuff():
    import tests.submod.absstuff as absstuff

    assert sorted(FunctionDiscovery.from_module(absstuff).keys()) == [9, 13, 18, 21]


def test_function_discovery(stuff_discovery):
    assert len(stuff_discovery.at_line(11)) == 3
    assert not stuff_discovery.at_line(69)
    assert len(stuff_discovery.at_line(71)) == 1
    assert not stuff_discovery.at_line(72)
    assert len(stuff_discovery.at_line(86)) == 1
    assert len(stuff_discovery.at_line(89)) == 1


@pytest.mark.parametrize(
    "alias",
    [
        "modulestuff",
        "__specialstuff__",
        "alias",
        "Stuff.propertystuff.getter",
        "Stuff.instancestuff",
        "Stuff.staticstuff",
        "Stuff.classstuff",
        "Stuff.decoratedstuff",
        "Stuff.doublydecoratedstuff",
        "Stuff.nestedstuff",
        "Stuff._Stuff__mangledstuff",
        "Stuff.__mangledstuff",
        "Stuff.generatorstuff",
    ],
)
def test_function_by_name(stuff_discovery, alias):
    assert stuff_discovery.by_name(alias)


@pytest.mark.parametrize(
    "orig,alias",
    [
        ("modulestuff", "alias"),
        ("AliasStuff.foo", "AliasStuff.bar"),
    ],
)
def test_function_aliasing(stuff_discovery, orig, alias):
    assert stuff_discovery.by_name(orig) == stuff_discovery.by_name(alias)


def test_function_property(stuff_discovery):
    original_property = stuff.Stuff.propertystuff
    assert isinstance(original_property, property)

    (original_fget,) = stuff_discovery.at_line(40)
    assert original_property.fget is original_fget


def test_function_staticmethod(stuff_discovery):
    original_sm = stuff.Stuff.staticstuff
    (original_func,) = stuff_discovery.at_line(29)
    assert original_sm is original_func


def test_function_classmethod(stuff_discovery):
    original_cm = stuff.Stuff.classstuff
    (original_func,) = stuff_discovery.at_line(33)
    assert original_cm.__func__ == original_func


def test_function_module_method(stuff_discovery):
    (original_func,) = stuff_discovery.at_line(6)
    assert stuff.modulestuff is original_func


def test_function_instance_method(stuff_discovery):
    cls = stuff.Stuff
    (original_func,) = stuff_discovery.at_line(36)
    assert _f(cls.instancestuff) is original_func


def test_function_decorated_method(stuff_discovery):
    method = stuff.Stuff.decoratedstuff
    (original_func,) = stuff_discovery.at_line(48)
    assert original_func in {_.cell_contents for _ in method.__closure__}


def test_function_mangled(stuff_discovery):
    original_method = stuff.Stuff._Stuff__mangledstuff
    (original_func,) = stuff_discovery.at_line(75)
    assert _f(original_method) is original_func


def test_discovery_after_external_wrapping(stuff):
    import ddtrace.vendor.wrapt as wrapt

    def wrapper(wrapped, inst, args, kwargs):
        pass

    wrapt.wrap_function_wrapper(stuff, "Stuff.instancestuff", wrapper)
    assert isinstance(stuff.Stuff.instancestuff, (wrapt.BoundFunctionWrapper, wrapt.FunctionWrapper))

    code = stuff.Stuff.instancestuff.__code__
    f = FunctionDiscovery(stuff)[36][0]

    assert isinstance(f, (wrapt.BoundFunctionWrapper, wrapt.FunctionWrapper))
    assert f.__code__ is code


def test_property_non_function_getter(stuff_discovery):
    with pytest.raises(ValueError):
        stuff_discovery.by_name("PropertyStuff.foo")


def test_undecorate():
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

    name, path = f.__code__.co_name, abspath(__file__)
    assert f is _undecorate(dddf, name, path)
    assert f is _undecorate(ddf, name, path)
    assert f is _undecorate(df, name, path)
    assert f is _undecorate(f, name, path)

    assert _undecorate(_undecorate, name, path) is _undecorate
