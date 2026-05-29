import importlib
from typing import Optional

from ddtrace.internal.settings._core import DDConfig


# Product config modules whose import registers them onto the root via Config.register().
# get() imports these before building so the tree is complete regardless of caller order.
_PRODUCT_MODULES = ("ddtrace.internal.settings.dynamic_instrumentation",)


class Config(DDConfig):
    """Root of the registry-driven configuration tree.

    Product configs attach under a namespace with ``Config.include(...)`` via
    ``Config.register(product, namespace)`` (Envier's standard nesting, as used by
    profiling/otel). Application code reads settings through one entry point::

        from ddtrace.internal.settings._registry import Config

        Config.get().dynamic_instrumentation.enabled

    ``get()`` eagerly imports the known product modules before building, so the
    returned singleton always contains every product regardless of the caller's
    import order.
    """

    _instance: "Optional[Config]" = None

    @classmethod
    def get(cls) -> "Config":
        if cls._instance is None:
            for module in _PRODUCT_MODULES:
                importlib.import_module(module)
            cls._instance = cls()
        return cls._instance

    @classmethod
    def register(cls, product: type, namespace: str) -> None:
        """Attach a product config under ``namespace`` and invalidate the cached singleton.

        The first registration of a namespace wins; a later call for the same namespace
        is a no-op (so module re-imports are safe).  The singleton cache is invalidated
        only when a new namespace is actually added, so a no-op re-registration cannot
        evict a singleton that consumers have already cached.  Because ``get()`` eagerly
        imports all product modules, registration order does not affect the built tree.
        """
        if not hasattr(cls, namespace):
            cls.include(product, namespace=namespace)
            cls._instance = None
