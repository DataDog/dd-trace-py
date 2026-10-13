"""Install a wrapping context on a module attribute as soon as the module is imported.

Kept beside context.py rather than in it, so that importing a WrappingContext does not drag in
the module watchdog and wrapt.
"""

from typing import Any
from typing import Callable

from wrapt import resolve_path

from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.wrapping.context import WrappingContext


log = get_logger(__name__)

# Depth cap when peeling wrapt proxies off a patched attribute.
_MAX_PROXY_DEPTH = 10

# Module hooks registered per target, so unwrapping can unregister them again.
_MODULE_HOOKS: dict[tuple[str, str], list[Callable[[Any], None]]] = {}
# Wrapping contexts currently installed, keyed by (module_name, name), so unwrap can use the
# instance that did the wrapping instead of re-resolving a possibly re-patched attribute.
_WRAPPING_CONTEXTS: dict[tuple[str, str], WrappingContext] = {}
# Contexts a reload superseded. Their functions stay wrapped, so aliases, subclasses and live
# instances that still hold them keep their instrumentation; retained so unwrap releases them too.
_SUPERSEDED_CONTEXTS: dict[tuple[str, str], list[WrappingContext]] = {}


def _module_name(module: Any) -> str:
    return module if isinstance(module, str) else module.__name__


def _unregister_module_hooks(module: Any, name: str) -> None:
    module_name = _module_name(module)
    for hook in _MODULE_HOOKS.pop((module_name, name), ()):
        ModuleWatchdog.unregister_module_hook(module_name, hook)


def target_function(module: Any, name: str) -> Any:
    """Resolve module.name to the plain function a wrapping context can bind to.

    Binding to a wrapt proxy is unrecoverable: getattr builds a fresh BoundFunctionWrapper every
    time, so the registration can never be found again and unwrap leaves the bytecode rewritten.
    """
    (parent, attribute, original) = resolve_path(module, name)
    # Read what the owner actually holds; getattr would run the descriptor protocol.
    try:
        original = parent.__dict__[attribute]
    except (AttributeError, KeyError, TypeError):
        pass
    for _ in range(_MAX_PROXY_DEPTH):
        # Peel the whole chain: a functools.wraps decorator is a function too, so stopping at the
        # first one binds to it, and its frame holds only args/kwargs.
        wrapped = getattr(original, "__wrapped__", None)
        if wrapped is None:
            break
        original = wrapped
    return original


def try_wrap_context(module_name: str, name: str, context_cls: type[WrappingContext]) -> None:
    """Lazily bytecode-wrap module_name.name with a wrapping context.

    Unlike a wrapt wrapper this leaves no frame of ours in the traceback of an exception that
    merely passes through the hook.
    """

    def _(module: Any) -> None:
        key = (module_name, name)
        try:
            target = target_function(module, name)
            installed = _WRAPPING_CONTEXTS.get(key)
            if installed is not None:
                # _wrapped_ref, not __wrapped__: the property raises once the old function has been
                # collected, which is precisely the reload case this has to handle.
                if installed._wrapped_ref() is target:
                    # Already wrapped. Re-registering the same context type raises, so stay a
                    # no-op, the way a repeated wrapt patch does.
                    return
                # Reloaded: rebind, but leave the old function wrapped, since aliases and live
                # instances still call it. Retained so unwrap reaches it, pruned once collected.
                del _WRAPPING_CONTEXTS[key]
                superseded = _SUPERSEDED_CONTEXTS.setdefault(key, [])
                superseded[:] = [c for c in superseded if c._wrapped_ref() is not None]
                superseded.append(installed)
            context = context_cls(target)
            context.wrap()
            _WRAPPING_CONTEXTS[key] = context
        except Exception:
            # Bytecode rewriting can fail on shapes the bytecode library cannot round-trip.
            # Losing our hook is acceptable; breaking the application's own patching is not.
            log.debug("Cannot wrap %s.%s with a wrapping context", module_name, name, exc_info=True)

    _MODULE_HOOKS.setdefault((module_name, name), []).append(_)
    ModuleWatchdog.register_module_hook(module_name, _)


def try_unwrap_context(module: Any, name: str) -> None:
    """Release the wrapping context installed on module.name by try_wrap_context.

    Unwraps the retained instances, not the attribute: an integration may have wrapt-wrapped over
    it, and unwrapping that proxy no-ops, leaving bytecode that the next patch rewrites again.
    """
    _unregister_module_hooks(module, name)
    key = (_module_name(module), name)
    contexts = _SUPERSEDED_CONTEXTS.pop(key, [])
    current = _WRAPPING_CONTEXTS.pop(key, None)
    if current is not None:
        contexts.append(current)
    for context in contexts:
        try:
            context.unwrap()
        except Exception:
            # A superseded context whose function has already been collected raises here.
            log.debug("ERROR unwrapping context %s.%s ", module, name, exc_info=True)
