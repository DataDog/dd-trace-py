"""Adapters that apply each dd-trace-py wrapping mechanism transparently.

Every adapter installs a *no-op* wrapper so that, if the mechanism is correct,
the wrapped callable is observationally identical to the original. The four
mechanisms attach differently (in-place __code__ mutation vs. returning a new
object) and bind differently to methods, so each adapter knows how to:

  * ``wrap_function(fn) -> callable``  -- for module funcs, closures, lambdas,
    callable-instance ``__call__`` targets, and contextmanager underlyings.
  * ``install_method(cls, attr, binding)`` -- mutate a class so ``cls.attr`` is
    wrapped, preserving instance/classmethod/staticmethod binding.

All adapters are pure transparent pass-throughs.
"""

import types

import pytest

from ddtrace.internal.wrapping import wrap as _internal_wrap
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.trace import tracer


# Internal wrap() / WrappingContext wrapper signature: (wrapped, args, kwargs)
def _noop(wrapped, args, kwargs):
    return wrapped(*args, **kwargs)


# wrapt wrapper signature: (wrapped, instance, args, kwargs)
def _noop_wrapt(wrapped, instance, args, kwargs):
    return wrapped(*args, **kwargs)


class _NoopWrappingContext(WrappingContext):
    """A WrappingContext that does nothing on enter/return/exit."""


def _underlying(cls, attr):
    """Return the raw function object behind a (possibly classmethod/staticmethod) attr."""
    raw = cls.__dict__[attr]
    return getattr(raw, "__func__", raw)


# Each adapter is a plain class exposing ``name``, ``wrap_function(fn)`` and
# ``install_method(cls, attr, binding)`` (see the module docstring); they are
# collected by ``name`` into ALL_MECHANISMS below.
class InternalWrap:
    name = "internal_wrap"

    def wrap_function(self, fn):
        _internal_wrap(fn, _noop)  # mutates __code__ in place
        return fn

    def install_method(self, cls, attr, binding):
        _internal_wrap(_underlying(cls, attr), _noop)


class WrappingContextMech:
    name = "wrapping_context"

    def wrap_function(self, fn):
        _NoopWrappingContext(fn).wrap()  # mutates in place
        return fn

    def install_method(self, cls, attr, binding):
        _NoopWrappingContext(_underlying(cls, attr)).wrap()


class TracerWrap:
    name = "tracer_wrap"

    def wrap_function(self, fn):
        return tracer.wrap()(fn)

    def install_method(self, cls, attr, binding):
        raw = cls.__dict__[attr]
        if binding == "classmethod":
            cls_attr = classmethod(tracer.wrap()(raw.__func__))
        elif binding == "staticmethod":
            cls_attr = staticmethod(tracer.wrap()(raw.__func__))
        else:  # instance_method (also __call__)
            cls_attr = tracer.wrap()(raw)
        setattr(cls, attr, cls_attr)


class WraptWrap:
    # dd-trace-py's exposed wrapt-based wrapping API is ``trace_utils.wrap``
    # (re-exported at ``ddtrace.contrib.trace_utils.wrap``), which *is*
    # ``wrapt.wrap_function_wrapper`` -- the entry point ~60 contrib integrations
    # call. We drive that here rather than the raw ``wrapt.FunctionWrapper``
    # constructor, so the matrix exercises our API footprint, not wrapt internals.
    # ``wrap_function_wrapper`` resolves a *named* target and installs a
    # FunctionWrapper, so a bare function is hosted on a namespace to give it a name.
    name = "wrapt"

    def wrap_function(self, fn):
        from ddtrace.contrib.internal.trace_utils import wrap

        holder = types.SimpleNamespace(target=fn)
        wrap(holder, "target", _noop_wrapt)
        return holder.target

    def install_method(self, cls, attr, binding):
        from ddtrace.contrib.internal.trace_utils import wrap

        wrap(cls, attr, _noop_wrapt)


ALL_MECHANISMS = {m.name: m for m in (InternalWrap(), TracerWrap(), WraptWrap(), WrappingContextMech())}


def xfail_mechanism(*names, reason, condition=True):
    """Parametrize a test over every mechanism, strict-xfailing the named one(s).

    Use this in place of the ``mech`` fixture when a test is a known failure for a
    specific mechanism. It is plain ``pytest.mark.parametrize`` + ``pytest.param(marks=...)``
    -- the documented way to xfail one parametrized value -- so the test source shows
    exactly what is expected to fail and for which mechanism::

        @xfail_mechanism("internal_wrap", reason="G22: wrap() drops the generator return value")
        def test_x(mech): ...

    ``strict`` is always on: when the underlying defect is fixed the case XPASSes and
    fails CI, which is the signal to delete the marker. ``condition`` gates the xfail by
    interpreter version, e.g. ``condition=sys.version_info >= (3, 11)`` (when false the
    test simply runs and is expected to pass).
    """
    unknown = set(names) - set(ALL_MECHANISMS)
    if unknown:
        raise ValueError(f"unknown mechanism(s) {sorted(unknown)}; expected from {sorted(ALL_MECHANISMS)}")
    xfail = pytest.mark.xfail(reason=reason, strict=True)
    return pytest.mark.parametrize(
        "mech",
        [
            pytest.param(mech, id=name, marks=xfail if condition and name in names else ())
            for name, mech in ALL_MECHANISMS.items()
        ],
    )


def wrap_property(mech, cls, attr):
    """Wrap a property's getter with ``mech``, preserving its setter/deleter.

    ``property`` is not an instance/class/static-method descriptor, so it does not
    go through ``install_method``; the realistic way to wrap one is to wrap its
    getter function and rebuild the property. ``property.__get__`` supplies ``self``.
    """
    prop = cls.__dict__[attr]
    setattr(cls, attr, property(mech.wrap_function(prop.fget), prop.fset, prop.fdel))
