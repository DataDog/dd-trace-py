# -*- coding: utf-8 -*-

import inspect

import pytest

from ddtrace.debugging import _safety


GLOBAL_VALUE = 42


def test_get_args():
    def assert_args(args):
        assert set(dict(_safety.get_args(inspect.currentframe().f_back)).keys()) == args

    def assert_locals(_locals):
        assert set(dict(_safety.get_locals(inspect.currentframe().f_back)).keys()) == _locals | {
            "assert_args",
            "assert_locals",
        }

    def assert_globals(_globals):
        assert set(dict(_safety.get_globals(inspect.currentframe().f_back)).keys()) == _globals

    def arg_and_kwargs(a, **kwargs):
        assert_args({"a", "kwargs"})
        assert_locals(set())

    def arg_and_args_and_kwargs(a, *ars, **kwars):
        assert_args({"a", "ars", "kwars"})
        assert_locals(set())

    def args_and_kwargs(*ars, **kwars):
        assert_args({"ars", "kwars"})
        assert_locals(set())

    def args(*ars):
        assert_args({"ars"})
        assert_locals(set())

    # `co_varnames` lays out keyword-only arguments BEFORE
    # `*args` / `**kwargs`. The previous nargs offset
    # `co_argcount + VARARGS + VARKEYWORDS` omitted `co_kwonlyargcount`,
    # so for `def f(a, *args, c, **kwargs)` keyword-only `c` was
    # mis-classified as positional and the actual `args` / `kwargs`
    # objects fell out of `get_args` entirely (and got reported by
    # `get_locals` instead).
    def kwonly_and_args_and_kwargs(a, *ars, c, **kwars):
        local = 1  # noqa
        assert_args({"a", "ars", "c", "kwars"})
        assert_locals({"local"})

    def kwonly_only(a, *, c, d):
        local = 1  # noqa
        assert_args({"a", "c", "d"})
        assert_locals({"local"})

    def referenced_globals():
        global GLOBAL_VALUE
        a = GLOBAL_VALUE >> 1  # noqa

        assert_globals({"GLOBAL_VALUE"})

    arg_and_kwargs(1, b=2)
    arg_and_args_and_kwargs(1, 42, b=2)
    args_and_kwargs()
    args()
    kwonly_and_args_and_kwargs(1, 42, c=3, extra=9)
    kwonly_only(1, c=3, d=4)
    referenced_globals()


# ---- Side effects ----


class SideEffects(object):
    class SideEffect(Exception):
        pass

    def __getattribute__(self, name):
        raise SideEffects.SideEffect()

    def __get__(self, instance, owner):
        raise self.SideEffect()

    @property
    def property_with_side_effect(self):
        raise self.SideEffect()


def test_get_fields_side_effects():
    assert _safety.get_fields(SideEffects()) == {}


# ---- Slots ----


def test_get_fields_slots():
    class A(object):
        __slots__ = ["a"]

        def __init__(self):
            self.a = "a"

    class B(A):
        __slots__ = ["b"]

        def __init__(self):
            super(B, self).__init__()
            self.b = "b"

    assert _safety.get_fields(A()) == {"a": "a"}
    assert _safety.get_fields(B()) == {"a": "a", "b": "b"}


def test_safe_dict():
    # Found in the FastAPI test suite
    class Foo(object):
        @property
        def __dict__(self):
            raise NotImplementedError()

    with pytest.raises(AttributeError):
        _safety._safe_dict(Foo())


def test_get_locals_freevars():
    """get_locals should include closure variables (freevars and cellvars)."""
    captured = []

    def outer():
        cell = 42  # cellvar captured by inner

        def inner():
            captured.append(inspect.currentframe())
            return cell  # freevar inside inner

        inner()

    outer()
    frame = captured[0]
    local_names = {name for name, _ in _safety.get_locals(frame)}
    assert "cell" in local_names


def test_get_locals_cellvars():
    """get_locals on the outer frame should expose cellvars."""
    outer_frames = []

    def outer():
        cell = 99  # cellvar
        outer_frames.append(inspect.currentframe())

        def inner():
            return cell

        inner()

    outer()
    frame = outer_frames[0]
    local_names = {name for name, _ in _safety.get_locals(frame)}
    assert "cell" in local_names


def test_get_globals_returns_referenced_globals():
    """get_globals should return names referenced as globals in the frame's code."""
    frames = []

    def capture():
        _ = GLOBAL_VALUE  # references GLOBAL_VALUE via LOAD_GLOBAL
        frames.append(inspect.currentframe())

    capture()
    result = dict(_safety.get_globals(frames[0]))
    assert "GLOBAL_VALUE" in result
    assert result["GLOBAL_VALUE"] == GLOBAL_VALUE


def test_getattr_or_exception_returns_exception():
    """getattr_or_exception should return the exception when attribute access fails."""

    class Boom:
        @property
        def bad(self):
            raise ValueError("boom")

    obj = Boom()
    result = _safety.getattr_or_exception(obj, "bad")
    assert isinstance(result, Exception)


def test_getattr_or_exception_missing_attribute():
    """getattr_or_exception returns AttributeError for missing attributes."""
    result = _safety.getattr_or_exception(object(), "nonexistent")
    assert isinstance(result, AttributeError)


# ---- Static type attribute lookup ----


def test_safe_get_type_attr_walks_mro():
    """Inherited class attributes are found, nearest definition first."""

    class A:
        marker = "a"

    class B(A):
        pass

    class C(B):
        marker = "c"

    assert _safety.safe_get_type_attr(B, "marker") == "a"
    assert _safety.safe_get_type_attr(C, "marker") == "c"
    assert _safety.safe_get_type_attr(A, "missing", "fallback") == "fallback"


def test_safe_get_type_attr_does_not_invoke_descriptors():
    """A descriptor holding the name is returned, not called."""

    class WithDescriptor:
        attr = SideEffects()

    result = _safety.safe_get_type_attr(WithDescriptor, "attr")
    assert isinstance(result, SideEffects)


def test_safe_get_type_attr_refuses_hostile_mapping():
    """A non-builtin __dict__ is skipped rather than indexed.

    safe_get_type_attr is generic, so it may be handed something that only
    claims to be a class. Indexing a mapping with a Python __getitem__ would
    run arbitrary code, which is exactly what this module exists to avoid.
    """

    class HostileMapping:
        def __getitem__(self, name):
            raise SideEffects.SideEffect()

    class Impostor:
        __mro__ = ()
        __dict__ = HostileMapping()  # pyright: ignore[reportAssignmentType]

    impostor = Impostor()
    impostor.__mro__ = (impostor,)

    # Sanity check: the mapping really would raise if it were indexed.
    with pytest.raises(SideEffects.SideEffect):
        object.__getattribute__(impostor, "__dict__")["anything"]

    assert _safety.safe_get_type_attr(impostor, "anything", "fallback") == "fallback"
