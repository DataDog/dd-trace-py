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
    name = "wrapt"

    def wrap_function(self, fn):
        import wrapt

        return wrapt.FunctionWrapper(fn, _noop_wrapt)

    def install_method(self, cls, attr, binding):
        import wrapt

        wrapt.wrap_function_wrapper(cls, attr, _noop_wrapt)


ALL_MECHANISMS = {m.name: m for m in (InternalWrap(), TracerWrap(), WraptWrap(), WrappingContextMech())}


def wrap_property(mech, cls, attr):
    """Wrap a property's getter with ``mech``, preserving its setter/deleter.

    ``property`` is not an instance/class/static-method descriptor, so it does not
    go through ``install_method``; the realistic way to wrap one is to wrap its
    getter function and rebuild the property. ``property.__get__`` supplies ``self``.
    """
    prop = cls.__dict__[attr]
    setattr(cls, attr, property(mech.wrap_function(prop.fget), prop.fset, prop.fdel))
