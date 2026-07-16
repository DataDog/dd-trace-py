import atexit
from collections import defaultdict
from collections import deque
from importlib.metadata import entry_points
from itertools import chain
import sys
import typing as t
from typing import Protocol  # noqa:F401

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.telemetry import report_configuration
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.uwsgi import check_uwsgi
from ddtrace.internal.uwsgi import uWSGIConfigDeprecationWarning
from ddtrace.internal.uwsgi import uWSGIConfigError
from ddtrace.internal.uwsgi import uWSGIMasterProcess


log = get_logger(__name__)

# The ddtrace.products entry-point group is currently internal-only. We reject
# plugins from any distribution other than "ddtrace" to prevent supply-chain
# attacks where a malicious third-party package registers a plugin that would be
# automatically loaded into every instrumented process.
# Distribution names are matched exactly (case-insensitively); module paths are
# matched as prefixes so all ddtrace.* submodules are covered.
_TRUSTED_PRODUCT_DISTRIBUTIONS = frozenset({"ddtrace"})
_TRUSTED_PRODUCT_MODULE_PREFIXES = frozenset({"ddtrace."})


if sys.version_info >= (3, 10):

    def get_product_entry_points() -> list[t.Any]:
        return list(entry_points(group="ddtrace.products"))

else:

    def get_product_entry_points() -> list[t.Any]:
        return [ep for _, eps in entry_points().items() for ep in eps if ep.group == "ddtrace.products"]


class Product(Protocol):
    requires: list[str]

    def post_preload(self) -> None: ...

    def enabled(self) -> bool: ...

    def start(self) -> None: ...

    def restart(self, join: bool = False) -> None: ...

    def stop(self, join: bool = False) -> None: ...


class ProductManager:
    __products__: dict[str, Product] = {}  # All discovered products

    def __init__(self) -> None:
        self._products: t.Optional[list[tuple[str, Product]]] = None  # Topologically sorted products
        self._failed: set[str] = set()

    def _load_products(self) -> None:
        for product_plugin in get_product_entry_points():
            name = product_plugin.name
            log.debug("Discovered product plugin '%s'", name)

            # Check both the distribution name and the entry point module path. The
            # distribution name check is a fast first filter, but it is self-declared
            # and can be spoofed. The module path check is the stronger guard: a
            # plugin whose code does not live under the ddtrace namespace cannot be
            # part of the trusted ddtrace package.
            module_path, _, _ = product_plugin.value.partition(":")
            # dist may be absent on Python < 3.10 with the legacy entry_points() API;
            # in that case skip the dist check and rely solely on the module path. A
            # dist name that is explicitly None (malformed metadata) is treated as
            # untrusted.
            dist = getattr(product_plugin, "dist", None)
            # Three cases:
            #  - dist attribute absent (Python < 3.10): no dist info, skip dist check
            #  - dist is None: no dist info available, skip dist check
            #  - dist present and not None: check Name; None Name means malformed metadata → reject
            dist_name: t.Optional[str] = None
            if not hasattr(product_plugin, "dist") or dist is None:
                trusted_dist = True
            else:
                dist_name = dist.metadata["Name"]
                trusted_dist = dist_name is not None and dist_name.lower() in _TRUSTED_PRODUCT_DISTRIBUTIONS
            trusted_module = any(module_path.startswith(p) for p in _TRUSTED_PRODUCT_MODULE_PREFIXES)
            if not (trusted_dist and trusted_module):
                log.warning(
                    "Refusing to load product plugin '%s' from untrusted distribution '%s' (module: %s)",
                    name,
                    dist_name,
                    module_path,
                )
                continue

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

    def _sort_products(self) -> list[tuple[str, Product]]:
        # Data structures for topological sorting
        q: deque[str] = deque()  # Queue of products with no dependencies
        g = defaultdict(list)  # Graph of dependencies
        f: dict[str, set] = {}  # Remaining dependencies for each product

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
    def products(self) -> list[tuple[str, Product]]:
        if self._products is None:
            self._products = self._sort_products()
        return self._products

    def start_products(self) -> None:
        failed: set[str] = set()

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
                if not product.enabled():
                    log.debug("Product '%s' is not enabled, skipping", name)
                    continue
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
        failed: set[str] = set()

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
                if not product.enabled():
                    continue
                product.stop(join=join)
                log.debug("Stopped product '%s'", name)
                telemetry_writer.product_activated(name.replace("-", "_"), False)
            except Exception:
                log.exception("Failed to stop product '%s'", name)

    def exit_products(self, join: bool = False) -> None:
        for name, product in reversed(self.products):
            try:
                if not product.enabled():
                    continue
                if (skip_exit := getattr(product, "skip_exit", None)) is not None and skip_exit():
                    log.debug("Skipping stop on exit for product '%s'", name)
                    continue
                product.stop(join=join)
                log.debug("Stopped product '%s' on exit", name)
            except Exception:
                try:
                    log.exception("Failed to stop product '%s' on exit", name)
                except Exception:  # nosec: B110
                    pass

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

        except uWSGIConfigDeprecationWarning:
            log.warning("uWSGI configuration deprecation warning", exc_info=True)
            self._do_products()

        except Exception:
            log.exception("Failed to check uWSGI configuration")

        # Ordinary process
        else:
            self._do_products()

    def is_enabled(self, product_name: str) -> bool:
        if (product := self.__products__.get(product_name)) is None:
            return False

        return product.enabled()


manager = ProductManager()
