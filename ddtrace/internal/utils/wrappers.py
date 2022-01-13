import inspect
from typing import Any
from typing import Callable
from typing import Optional
from typing import TYPE_CHECKING
from typing import TypeVar

from ddtrace.vendor import wrapt

from .deprecation import deprecated


if TYPE_CHECKING:
    import ddtrace


F = TypeVar("F", bound=Callable[..., Any])


def iswrapped(obj, attr=None):
    # type: (Any, Optional[str]) -> bool
    """Returns whether an attribute is wrapped or not."""
    if attr is not None:
        obj = getattr(obj, attr, None)
    return hasattr(obj, "__wrapped__") and isinstance(obj, wrapt.ObjectProxy)


def unwrap(obj, attr):
    # type: (Any, str) -> None
    f = getattr(obj, attr)
    setattr(obj, attr, f.__wrapped__)


@deprecated("`wrapt` library is used instead", version="1.0.0")
def safe_patch(
    patchable,  # type: Any
    key,  # type: str
    patch_func,  # type: Callable[[F, str, dict, ddtrace.Tracer], F]
    service,  # type: str
    meta,  # type: dict
    tracer,  # type: ddtrace.Tracer
):
    # type: (...) -> None
    """takes patch_func (signature: takes the orig_method that is
    wrapped in the monkey patch == UNBOUND + service and meta) and
    attach the patched result to patchable at patchable.key

    - If this is the module/class we can rely on methods being unbound, and just have to
      update the __dict__
    - If this is an instance, we have to unbind the current and rebind our
      patched method
    - If patchable is an instance and if we've already patched at the module/class level
      then patchable[key] contains an already patched command!

    To workaround this, check if patchable or patchable.__class__ are ``_dogtraced``
    If is isn't, nothing to worry about, patch the key as usual
    But if it is, search for a '__dd_orig_{key}' method on the class, which is
    the original unpatched method we wish to trace.
    """

    def _get_original_method(thing, key):
        orig = None
        if hasattr(thing, "_dogtraced"):
            # Search for original method
            orig = getattr(thing, "__dd_orig_{}".format(key), None)
        else:
            orig = getattr(thing, key)
            # Set it for the next time we attempt to patch `thing`
            setattr(thing, "__dd_orig_{}".format(key), orig)

        return orig

    if inspect.isclass(patchable) or inspect.ismodule(patchable):
        orig = _get_original_method(patchable, key)
        if not orig:
            # Should never happen
            return
    elif hasattr(patchable, "__class__"):
        orig = _get_original_method(patchable.__class__, key)
        if not orig:
            # Should never happen
            return
    else:
        return

    dest = patch_func(orig, service, meta, tracer)

    if inspect.isclass(patchable) or inspect.ismodule(patchable):
        setattr(patchable, key, dest)
    elif hasattr(patchable, "__class__"):
        setattr(patchable, key, dest.__get__(patchable, patchable.__class__))  # type: ignore[attr-defined]
