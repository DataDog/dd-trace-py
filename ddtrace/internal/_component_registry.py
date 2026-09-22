"""Registry mapping a component name (e.g. "anthropic") to a product-owned object for that
component, so that code in one zone (typically ddtrace.contrib) can obtain the object without
importing the product package that defines it.

See ddtrace/internal/README.md ("Component Registry") for the full usage guide: when to reach
for this instead of importing a product directly, and why the objects stored here are called
"component handles" rather than "integrations" (ddtrace.contrib already uses "integration" to
mean something else - the per-library patch module itself).
"""

from typing import Any
from typing import Callable
from typing import Optional


# component name -> factory(component_config) -> handle
_factories: dict[str, Callable[[Any], Any]] = {}
# component name -> handle, cached after first get_or_create() call
_handles: dict[str, Any] = {}
_loader: Optional[Callable[[], None]] = None
_loader_invoked = False


def register_factory(component: str, factory: Callable[[Any], Any]) -> None:
    """Register how to build the handle for `component`. Called by the owning product, not by
    the code that will eventually look the handle up.
    """
    _factories[component] = factory


def set_loader(loader: Callable[[], None]) -> None:
    """Register a callable that populates this registry (via register_factory()) on first use.

    Deferred rather than run eagerly so the module that owns the loader (e.g. ddtrace._monkey)
    can point at it at its own import time without triggering the import the loader performs
    until a lookup actually needs it. This matters when the owning product's package is heavy or
    order-sensitive to import - see ddtrace/internal/README.md for the concrete case that made
    this necessary.
    """
    global _loader
    _loader = loader


def get_or_create(component: str, component_config: Any) -> Optional[Any]:
    """Return the cached handle for `component`, building and caching it via its registered
    factory on first call. Returns None if no factory has been registered for `component`
    (including after running the loader, if one is set).
    """
    global _loader_invoked
    if component not in _handles:
        factory = _factories.get(component)
        if factory is None and _loader is not None and not _loader_invoked:
            _loader_invoked = True
            _loader()
            factory = _factories.get(component)
        if factory is None:
            return None
        _handles[component] = factory(component_config)
    return _handles[component]
