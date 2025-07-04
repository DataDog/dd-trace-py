import atexit
from collections import defaultdict
from collections import deque
from itertools import chain
import sys
import typing as t

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import report_configuration
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.uwsgi import check_uwsgi
from ddtrace.internal.uwsgi import uWSGIConfigError
from ddtrace.internal.uwsgi import uWSGIMasterProcess
from ddtrace.settings._core import DDConfig


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
        self._products: t.Optional[t.List[t.Tuple[str, Product]]] = None  # Topologically sorted products
        self._failed: t.Set[str] = set()

    def _load_products(self) -> None:
        for product_plugin in entry_points(group="ddtrace.products"):
            name = product_plugin.name
            log.debug("Discovered product plugin '%s'", name)

            # Load the product protocol object
            try:
                product: Product = product_plugin.load()
            except Exception:
                log.exception("Failed to load product plugin '%s'", name)
                self._failed.add(name)
                continue

            # Report configuration via telemetry
            if isinstance(config := getattr(product, "config", None), DDConfig):
                report_configuration(config)

            log.debug("Product plugin '%s' loaded successfully", name)

            self.__products__[name] = product

    def _sort_products(self) -> t.List[t.Tuple[str, Product]]:
        # Data structures for topological sorting
        q: t.Deque[str] = deque()  # Queue of products with no dependencies
        g = defaultdict(list)  # Graph of dependencies
        f: t.Dict[str, set] = {}  # Remaining dependencies for each product

        # Include failed products in the graph to avoid reporting false circular
        # dependencies when a product fails to load
        for name, product in chain(self.__products__.items(), ((p, None) for p in self._failed)):
            product_requires = getattr(product, "requires", [])
            if not product_requires:
                q.append(name)
            else:
                f[name] = set(product_requires)
                for r in product_requires:
                    g[r].append(name)

        # Determine the product (topological) ordering
        ordering = []
        while q:
            n = q.popleft()
            ordering.append(n)
            for p in g[n]:
                f[p].remove(n)
                if not f[p]:
                    q.append(p)
                    del f[p]

        if f:
            log.error(
                "Circular dependencies among products detected. These products won't be enabled: %s.", list(f.keys())
            )

        return [(name, self.__products__[name]) for name in ordering if name not in f and name in self.__products__]

    @property
    def products(self) -> t.List[t.Tuple[str, Product]]:
        if self._products is None:
            self._products = self._sort_products()
        return self._products

    def start_products(self) -> None:
        failed: t.Set[str] = set()

        for name, product in self.products:
            # Check that no required products have failed
            failed_requirements = failed & set(getattr(product, "requires", []))
            if failed_requirements:
                log.error(
                    "Product '%s' won't start because these dependencies failed to start: %s", name, failed_requirements
                )
                failed.add(name)
                continue

            try:
                product.start()
                log.debug("Started product '%s'", name)
                telemetry_writer.product_activated(name.replace("-", "_"), True)
            except Exception:
                log.exception("Failed to start product '%s'", name)
                failed.add(name)

    def before_fork(self) -> None:
        for name, product in self.products:
            try:
                if (hook := getattr(product, "before_fork", None)) is None:
                    continue
                hook()
                log.debug("Before-fork hook for product '%s' executed", name)
            except Exception:
                log.exception("Failed to execute before-fork hook for product '%s'", name)

    def restart_products(self, join: bool = False) -> None:
        failed: t.Set[str] = set()

        for name, product in self.products:
            failed_requirements = failed & set(getattr(product, "requires", []))
            if failed_requirements:
                log.error(
                    "Product '%s' won't restart because these dependencies failed to restart: %s",
                    name,
                    failed_requirements,
                )
                failed.add(name)
                continue

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
                telemetry_writer.product_activated(name.replace("-", "_"), False)
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

    def _do_products(self) -> None:
        # Start all products
        self.start_products()

        # Execute before fork hooks
        forksafe.register_before_fork(self.before_fork)

        # Restart products on fork
        forksafe.register(self.restart_products)

        # Stop all products on exit
        atexit.register(self.exit_products)

    def run_protocol(self) -> None:
        self._load_products()

        # uWSGI support
        try:
            check_uwsgi(worker_callback=forksafe.ddtrace_after_in_child)
        except uWSGIMasterProcess:
            # We are in the uWSGI master process, we should handle products in the
            # post-fork callback
            @forksafe.register
            def _() -> None:
                self._do_products()
                forksafe.unregister(_)

        except uWSGIConfigError:
            log.error("uWSGI configuration error", exc_info=True)
        except Exception:
            log.exception("Failed to check uWSGI configuration")

        # Ordinary process
        else:
            self._do_products()

    def is_enabled(self, product_name: str, enabled_attribute: str = "enabled") -> bool:
        if (product := self.__products__.get(product_name)) is None:
            return False

        if (config := getattr(product, "config", None)) is None:
            return False

        return getattr(config, enabled_attribute, False)


manager = ProductManager()
