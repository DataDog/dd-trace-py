"""Neutral registry mapping a component name (e.g. "anthropic") to its LLMObs integration
object. Lets contrib patch modules obtain their integration instance without importing
ddtrace.llmobs directly. Products populate this via register_factory(); ddtrace._monkey
triggers that registration once at import time.
"""

from typing import Any
from typing import Callable
from typing import Optional


_factories: dict[str, Callable[[Any], Any]] = {}
_instances: dict[str, Any] = {}
_loader: Optional[Callable[[], None]] = None
_loader_invoked = False


def register_factory(component: str, factory: Callable[[Any], Any]) -> None:
    _factories[component] = factory


def set_loader(loader: Callable[[], None]) -> None:
    """Register a callable that populates this registry via register_factory() on first use.

    Deferred rather than run eagerly so the module that owns the loader (e.g. ddtrace._monkey) can
    register it at its own import time without triggering the (possibly heavy, order-sensitive)
    import that the loader performs until a lookup actually needs it.
    """
    global _loader
    _loader = loader


def get_or_create(component: str, integration_config: Any) -> Optional[Any]:
    global _loader_invoked
    if component not in _instances:
        factory = _factories.get(component)
        if factory is None and _loader is not None and not _loader_invoked:
            _loader_invoked = True
            _loader()
            factory = _factories.get(component)
        if factory is None:
            return None
        _instances[component] = factory(integration_config)
    return _instances[component]
