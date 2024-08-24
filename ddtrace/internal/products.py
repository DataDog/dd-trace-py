from collections import defaultdict
import sys
import typing as t

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

if sys.version_info < (3, 10):
    from importlib_metadata import entry_points
else:
    from importlib.metadata import entry_points

try:
    from typing import Protocol  # noqa:F401
except ImportError:
    from typing_extensions import Protocol  # type: ignore[assignment]


class Product(Protocol):
    requires: t.List[str]

    def post_preload(self) -> None:
        ...

    def start(self) -> None:
        ...

    def restart(self, join: bool = False) -> None:
        ...

    def stop(self, join: bool = False) -> None:
        ...

    def at_exit(self, join: bool = False) -> None:
        ...


class ProductManager:
    __products__: t.Dict[str, Product] = {}  # All discovered products

    def __init__(self) -> None:
        q = []
        g = defaultdict(list)
        f = {}
        for product_plugin in tuple(entry_points(group="ddtrace.products"))[::-1]:
            name = product_plugin.name
            log.debug("Discovered product plugin '%s'", name)

            # Load the product protocol object
            try:
                product: Product = product_plugin.load()
            except Exception:
                log.exception("Failed to load product plugin '%s'", name)
                continue

            product_requires = getattr(product, "requires", [])
            if not product_requires:
                q.append(name)
            else:
                f[name] = list(product_requires)
                for r in product_requires:
                    g[r].append(name)

            log.debug("Product plugin '%s' loaded successfully", name)

            self.__products__[name] = product

        # Determine the product ordering
        ordering = []
        while q:
            n = q.pop()
            ordering.append(n)
            for p in g[n]:
                f[p].remove(n)
                if not f[p]:
                    q.append(p)
                    del f[p]

        if f:
            log.error("Circular dependencies detected for these products: %s", list(f.keys()))

        self.products = [(name, self.__products__[name]) for name in ordering]

    def start_products(self) -> None:
        for name, product in self.products:
            try:
                product.start()
                log.debug("Started product '%s'", name)
            except Exception:
                log.exception("Failed to start product '%s'", name)

    def restart_products(self, join: bool = False) -> None:
        for name, product in self.products:
            try:
                product.restart(join=join)
                log.debug("Restarted product '%s'", name)
            except Exception:
                log.exception("Failed to restart product '%s'", name)

    def stop_products(self, join: bool = False) -> None:
        for name, product in reversed(self.products):
            try:
                product.stop(join=join)
                log.debug("Stopped product '%s'", name)
            except Exception:
                log.exception("Failed to stop product '%s'", name)

    def exit_products(self, join: bool = False) -> None:
        for name, product in reversed(self.products):
            try:
                log.debug("Exiting product '%s'", name)
                product.at_exit(join=join)
            except Exception:
                log.exception("Failed to exit product '%s'", name)

    def post_preload_products(self) -> None:
        for name, product in self.products:
            try:
                product.post_preload()
                log.debug("Post-preload product '%s' done", name)
            except Exception:
                log.exception("Failed to post_preload product '%s'", name)


manager = ProductManager()
