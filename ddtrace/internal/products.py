import atexit
from collections import defaultdict
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from itertools import chain
import sys
from types import MappingProxyType
from types import ModuleType
import typing as t
from typing import Protocol

from ddtrace.internal import forksafe
from ddtrace.internal.entry_points import get_entry_points
from ddtrace.internal.entry_points import is_trusted
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.telemetry import report_configuration
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.uwsgi import check_uwsgi
from ddtrace.internal.uwsgi import uWSGIConfigDeprecationWarning
from ddtrace.internal.uwsgi import uWSGIConfigError
from ddtrace.internal.uwsgi import uWSGIMasterProcess


log = get_logger(__name__)


def get_product_entry_points() -> list[t.Any]:
    return get_entry_points("ddtrace.products")


class Product(Protocol):
    requires: list[str]

    def post_preload(self) -> None: ...

    def enabled(self) -> bool: ...

    def start(self) -> None: ...

    def restart(self, join: bool = False) -> None: ...

    def stop(self, join: bool = False) -> None: ...


class RegistrableSubscriber(Protocol):
    def register(self) -> None: ...

    def unregister(self) -> None: ...


@dataclass(frozen=True)
class SubscriberBundle:
    """Subscribers exported by one lazily imported product module.

    Subscriber classes must disable auto_register so importing the module does not
    activate listeners outside the product's lifecycle. Named listener registration
    supplies idempotency; bundles deliberately do not cache registration state.
    """

    module: str
    subscribers: tuple[str, ...]

    def _resolve(self, module: ModuleType) -> tuple[RegistrableSubscriber, ...]:
        # Resolve the whole declaration before changing listeners, so a misspelled
        # subscriber name cannot leave an otherwise valid bundle partly registered.
        return tuple(t.cast(RegistrableSubscriber, getattr(module, name)) for name in self.subscribers)

    def register(self) -> None:
        """Register the bundle, importing its module only when requested."""
        for subscriber in self._resolve(import_module(self.module)):
            subscriber.register()

    def unregister(self) -> None:
        """Remove the bundle's listeners after the owner has drained active contexts.

        Teardown never imports a previously unused product module. Products needing
        completion listeners for active requests must defer this operation.
        """
        module = sys.modules.get(self.module)
        if module is not None:
            for subscriber in reversed(self._resolve(module)):
                subscriber.unregister()


@dataclass(frozen=True)
class ProductRegistration:
    """Identify a product and declare its subscriber bundles in registration order.

    The product owns activation policy. A family selects one bundle; omitting it
    registers or unregisters every declared bundle. ProductManager discovery does
    not activate these declarations automatically.
    """

    name: str
    subscriber_bundles: Mapping[str, SubscriberBundle]

    def __post_init__(self) -> None:
        # Snapshot declarations so later mutation cannot change which listeners teardown owns.
        object.__setattr__(self, "subscriber_bundles", MappingProxyType(dict(self.subscriber_bundles)))

    def _bundles(self, family: t.Optional[str]) -> tuple[SubscriberBundle, ...]:
        if family is None:
            return tuple(self.subscriber_bundles.values())
        return (self.subscriber_bundles[family],)

    def register(self, family: t.Optional[str] = None) -> None:
        """Register all bundles or one family; an unknown family raises KeyError."""
        for bundle in self._bundles(family):
            bundle.register()

    def unregister(self, family: t.Optional[str] = None) -> None:
        """Unregister selected bundles in reverse order once their work has drained."""
        for bundle in reversed(self._bundles(family)):
            bundle.unregister()


class ProductManager:
    __products__: dict[str, Product] = {}  # All discovered products

    def __init__(self) -> None:
        self._products: t.Optional[list[tuple[str, Product]]] = None  # Topologically sorted products
        self._failed: set[str] = set()

    def _load_products(self) -> None:
        for product_plugin in get_product_entry_points():
            name = product_plugin.name
            log.debug("Discovered product plugin '%s'", name)

            if not is_trusted(product_plugin):
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
        started: list[tuple[str, Product]] = []

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
                started.append((name, product))
            except Exception:
                log.exception("Failed to start product '%s'", name)
                failed.add(name)
                continue

            # enabled() only says the lifecycle must run; a product can start without being active
            # (e.g. AppSec waiting on remote activation). Evaluate after start() to see load failures.
            try:
                activated = getattr(product, "activated", None)
                telemetry_writer.product_activated(name.replace("-", "_"), bool(activated()) if activated else True)
            except Exception:
                log.debug("Failed to report activation state for product '%s'", name, exc_info=True)

        # NOTE: Keep post_start hooks after the full start loop. RC uses
        # this barrier to collect dependent products before its first poll, and
        # start_products() may run only after a uWSGI fork.
        for name, product in started:
            try:
                if (hook := getattr(product, "post_start", None)) is None:
                    continue
                hook()
                log.debug("Post-start product '%s' done", name)
            except Exception:
                log.exception("Failed to post-start product '%s'", name)

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

        # Emit telemetry app-started now that products are loaded and their configuration has been
        # reported, so the app-started payload carries the full startup configuration (the native
        # worker builds it from state accumulated so far; see TelemetryWriter.app_started).
        telemetry_writer.app_started()

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
