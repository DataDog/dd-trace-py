from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import call
from unittest.mock import patch

import pytest

from ddtrace.internal.products import Product
from ddtrace.internal.products import ProductManager
from ddtrace.internal.products import SubscriberBundle
from ddtrace.internal.products import get_product_entry_points


class ProductManagerTest(ProductManager):
    def __init__(self, products, failed=set()) -> None:
        self._products = None
        self.__products__ = products
        self._failed = failed


class BaseProduct(Product):
    requires = []

    def __init__(self) -> None:
        self.started = self.restarted = self.stopped = self.exited = self.post_preloaded = False

    def post_preload(self) -> None:
        self.post_preloaded = True

    def enabled(self) -> bool:
        return True

    def start(self) -> None:
        self.started = True

    def restart(self, join: bool = False) -> None:
        self.restarted = True

    def stop(self, join: bool = False) -> None:
        self.stopped = True

    def at_exit(self, join: bool = False) -> None:
        self.exited = True


def test_product_manager_cycles():
    class A(BaseProduct):
        requires = ["b"]

    class B(BaseProduct):
        requires = ["a"]

    class C(BaseProduct):
        requires = ["d"]

    a = A()
    b = B()
    c = C()
    d = BaseProduct()

    manager = ProductManagerTest({"a": a, "b": b, "c": c, "d": d})
    manager.run_protocol()

    # a and b depend on each other, so they won't start
    assert not a.started and not b.started

    # c and d don't have cycles so they will start
    assert c.started and d.started


def test_product_manager_load_fail():
    class C(BaseProduct):
        requires = ["b"]

    a = BaseProduct()
    c = C()

    manager = ProductManagerTest({"a": a, "c": c}, failed={"b"})
    assert manager.products == [("a", a), ("c", c)]


def test_product_manager_start_fail():
    class B(BaseProduct):
        requires = ["a"]

        def start(self) -> None:
            raise RuntimeError()

    class C(BaseProduct):
        requires = ["b"]

    a = BaseProduct()
    b = B()
    c = C()

    manager = ProductManagerTest({"a": a, "b": b, "c": c})
    manager.run_protocol()

    # a will start
    assert a.started

    # b fails to start, so c won't start because it depends on b
    assert not b.started and not c.started


def test_product_manager_start():
    a = BaseProduct()
    manager = ProductManagerTest({"a": a})
    manager.run_protocol()
    assert a.started


@pytest.mark.subprocess
def test_product_manager_restart():
    import os

    from ddtrace.internal.products import Product
    from ddtrace.internal.products import ProductManager

    class ProductManagerTest(ProductManager):
        def __init__(self, products, failed=set()) -> None:
            self._products = None
            self.__products__ = products
            self._failed = failed

    class BaseProduct(Product):
        requires = []

        def __init__(self) -> None:
            self.started = self.restarted = self.stopped = self.exited = self.post_preloaded = False

        def enabled(self) -> bool:
            return True

        def post_preload(self) -> None:
            self.post_preloaded = True

        def start(self) -> None:
            self.started = True

        def restart(self, join: bool = False) -> None:
            self.restarted = True

        def stop(self, join: bool = False) -> None:
            self.stopped = True

        def at_exit(self, join: bool = False) -> None:
            self.exited = True

    a = BaseProduct()
    manager = ProductManagerTest({"a": a})
    manager.run_protocol()
    assert a.started
    assert not a.restarted

    pid = os.fork()
    if pid == 0:
        assert a.restarted
        os._exit(0)

    os.waitpid(pid, 0)


def _make_entry_point(name, dist_name, module_path, product_obj):
    ep = MagicMock()
    ep.name = name
    ep.value = f"{module_path}:module"
    ep.dist = MagicMock()
    ep.dist.metadata = {"Name": dist_name}
    ep.load.return_value = product_obj
    return ep


def test_load_products_trusted():
    product = BaseProduct()
    ep = _make_entry_point("tracer", "ddtrace", "ddtrace._trace.product", product)

    manager = ProductManager()
    manager.__products__ = {}
    with patch("ddtrace.internal.products.get_product_entry_points", return_value=[ep]):
        manager._load_products()

    assert "tracer" in manager.__products__


def test_load_products_trusted_mixed_case_dist():
    product = BaseProduct()
    ep = _make_entry_point("tracer", "DDTrace", "ddtrace._trace.product", product)

    manager = ProductManager()
    manager.__products__ = {}
    with patch("ddtrace.internal.products.get_product_entry_points", return_value=[ep]):
        manager._load_products()

    assert "tracer" in manager.__products__


def test_load_products_untrusted_dist():
    product = BaseProduct()
    ep = _make_entry_point("evil", "evil-package", "ddtrace._trace.product", product)

    manager = ProductManager()
    manager.__products__ = {}
    with patch("ddtrace.internal.products.get_product_entry_points", return_value=[ep]):
        manager._load_products()

    assert "evil" not in manager.__products__


def test_load_products_untrusted_dist_with_ddtrace_prefix():
    """A package named 'ddtrace-evil' must not be treated as trusted (exact match only)."""
    product = BaseProduct()
    ep = _make_entry_point("evil", "ddtrace-evil", "ddtrace._trace.product", product)

    manager = ProductManager()
    manager.__products__ = {}
    with patch("ddtrace.internal.products.get_product_entry_points", return_value=[ep]):
        manager._load_products()

    assert "evil" not in manager.__products__


def test_load_products_spoofed_dist_name():
    """A package that sets metadata Name='ddtrace' but ships code outside the ddtrace namespace."""
    product = BaseProduct()
    ep = _make_entry_point("evil", "ddtrace", "evil_package.product", product)

    manager = ProductManager()
    manager.__products__ = {}
    with patch("ddtrace.internal.products.get_product_entry_points", return_value=[ep]):
        manager._load_products()

    assert "evil" not in manager.__products__


def test_load_products_no_dist_metadata():
    """When dist is present but None, trust relies solely on the module path."""
    product = BaseProduct()
    ep = _make_entry_point("tracer", "ddtrace", "ddtrace._trace.product", product)
    ep.dist = None

    manager = ProductManager()
    manager.__products__ = {}
    with patch("ddtrace.internal.products.get_product_entry_points", return_value=[ep]):
        manager._load_products()

    assert "tracer" in manager.__products__


def test_load_products_no_dist_attribute():
    """When EntryPoint.dist is absent (Python < 3.10), trust relies solely on the module path."""
    product = BaseProduct()
    ep = _make_entry_point("tracer", "ddtrace", "ddtrace._trace.product", product)
    del ep.dist

    manager = ProductManager()
    manager.__products__ = {}
    with patch("ddtrace.internal.products.get_product_entry_points", return_value=[ep]):
        manager._load_products()

    assert "tracer" in manager.__products__


def test_load_products_no_dist_attribute_untrusted_module():
    """When EntryPoint.dist is absent and the module path is outside ddtrace, reject."""
    product = BaseProduct()
    ep = _make_entry_point("evil", "ddtrace", "evil_package.product", product)
    del ep.dist

    manager = ProductManager()
    manager.__products__ = {}
    with patch("ddtrace.internal.products.get_product_entry_points", return_value=[ep]):
        manager._load_products()

    assert "evil" not in manager.__products__


def test_product_manager_is_enabled():
    class DisabledProduct(BaseProduct):
        def enabled(self) -> bool:
            return False

    # Test when product doesn't exist
    manager = ProductManagerTest({})
    assert not manager.is_enabled("nonexistent")

    # Test when product exists and is enabled
    enabled_product = BaseProduct()
    manager = ProductManagerTest({"enabled": enabled_product})
    assert manager.is_enabled("enabled")

    # Test when product exists but is disabled
    disabled_product = DisabledProduct()
    manager = ProductManagerTest({"disabled": disabled_product})
    assert not manager.is_enabled("disabled")


def test_product_manager_reports_activated_state():
    class Inactive(BaseProduct):
        def activated(self) -> bool:
            return False

    class Active(BaseProduct):
        def activated(self) -> bool:
            return self.started

    manager = ProductManagerTest({"plain": BaseProduct(), "inactive": Inactive(), "active": Active()})
    with patch("ddtrace.internal.products.telemetry_writer") as telemetry_writer:
        manager.start_products()

    assert sorted(c.args for c in telemetry_writer.product_activated.call_args_list) == [
        ("active", True),
        ("inactive", False),
        ("plain", True),
    ]


def test_product_manager_activated_failure_does_not_fail_product():
    class Broken(BaseProduct):
        def activated(self) -> bool:
            raise RuntimeError()

    class Dependent(BaseProduct):
        requires = ["broken"]

    broken = Broken()
    dependent = Dependent()
    manager = ProductManagerTest({"broken": broken, "dependent": dependent})
    with patch("ddtrace.internal.products.telemetry_writer") as telemetry_writer:
        manager.start_products()

    assert broken.started
    assert dependent.started
    telemetry_writer.product_activated.assert_called_once_with("dependent", True)


def _remotely_startable_products():
    """The products that remote configuration can start, via their apm_tracing_rc handler."""
    names = set()
    for entry_point in get_product_entry_points():
        try:
            if hasattr(entry_point.load(), "apm_tracing_rc"):
                names.add(entry_point.name)
        except Exception:
            pass
    return sorted(names)


@pytest.mark.subprocess(
    env=dict(
        DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true",
        # Remote configuration must be enabled for its products to be set up,
        # but must not receive anything.
        DD_REMOTE_CONFIGURATION_ENABLED="true",
        DD_TRACE_AGENT_URL="http://127.0.0.1:1",
        # Products that cannot reach the agent fall back to agentless mode.
        DD_API_KEY="0" * 32,
        # Coverage preloads modules that import threading, which would hide the issue.
        COVERAGE_PROCESS_START=None,
        COVERAGE_PROCESS_CONFIG=None,
        COV_CORE_SOURCE=None,
        COV_CORE_CONFIG=None,
        COV_CORE_DATAFILE=None,
    ),
    parametrize={"REMOTE_PRODUCT": _remotely_startable_products()},
    timeout=60,
)
def test_remote_product_start_does_not_import_threading():
    """Starting a product from the remote config thread must not import threading.

    Module cloning unloads threading. A product started by remote configuration
    runs its start() on the remote config thread, and any module it imports for
    the first time is a fresh copy. On Python < 3.13, if that imports threading,
    the remote config thread becomes the main thread and the interpreter waits
    on it forever at exit.
    """
    import os
    import sys

    import ddtrace.auto  # noqa:F401
    from ddtrace.internal.products import manager
    from ddtrace.internal.threads import Thread

    name = os.environ["REMOTE_PRODUCT"]

    assert "threading" not in sys.modules

    errors = []

    def start():
        try:
            manager.__products__[name].start()
        except Exception as e:
            errors.append(e)

    worker = Thread(start, name="remote-config")
    worker.start()
    worker.join()

    # A product that fails to start does not exercise its start-up path.
    assert not errors, errors

    imported = "threading" in sys.modules

    # TODO: Enabling LLMObs runs git through a fresh copy of subprocess, which
    # imports threading on some Python versions (e.g. 3.10).
    if name == "llmobs" and imported:
        import pytest

        pytest.xfail("starting LLMObs imports subprocess, and with it threading")

    assert not imported


def test_product_bundles_register_after_each_successful_start() -> None:
    product = BaseProduct()
    first, second = Mock(spec=SubscriberBundle), Mock(spec=SubscriberBundle)
    product.subscriber_bundles = (first, second)
    calls = Mock()
    calls.attach_mock(first, "first")
    calls.attach_mock(second, "second")

    def assert_started() -> None:
        assert product.started

    first.register.side_effect = assert_started
    manager = ProductManagerTest({"product": product})
    manager.start_products()
    assert calls.mock_calls == [call.first.register(), call.second.register()]
    manager.stop_products()
    calls.reset_mock()
    manager.start_products()
    assert calls.mock_calls == [call.first.register(), call.second.register()]


@pytest.mark.parametrize("unavailable", ["disabled", "failed_start", "failed_dependency"])
def test_unavailable_products_do_not_register_bundles(unavailable: str, monkeypatch: pytest.MonkeyPatch) -> None:
    product = BaseProduct()
    bundle = Mock(spec=SubscriberBundle)
    product.subscriber_bundles = (bundle,)
    products = {"product": product}
    if unavailable == "disabled":
        monkeypatch.setattr(product, "enabled", Mock(return_value=False))
    elif unavailable == "failed_start":
        monkeypatch.setattr(product, "start", Mock(side_effect=RuntimeError("start failed")))
    else:
        product.requires = ["dependency"]
        dependency = BaseProduct()
        monkeypatch.setattr(dependency, "start", Mock(side_effect=RuntimeError("dependency failed")))
        products["dependency"] = dependency
    manager = ProductManagerTest(products)
    manager.start_products()
    bundle.register.assert_not_called()


def test_bundle_registration_failure_does_not_block_other_bundles_or_products() -> None:
    product, dependent = BaseProduct(), BaseProduct()
    first, second = Mock(spec=SubscriberBundle), Mock(spec=SubscriberBundle)
    first.register.side_effect = ImportError("unavailable")
    product.subscriber_bundles = (first, second)
    dependent.requires = ["product"]
    manager = ProductManagerTest({"product": product, "dependent": dependent})
    manager.start_products()
    first.register.assert_called_once_with()
    second.register.assert_called_once_with()
    assert dependent.started


@pytest.mark.parametrize("shutdown", ["stop_products", "exit_products"])
def test_product_bundles_unregister_after_stop(shutdown: str) -> None:
    product = BaseProduct()
    first, second = Mock(spec=SubscriberBundle), Mock(spec=SubscriberBundle)
    product.subscriber_bundles = (first, second)

    def assert_stopped() -> None:
        assert product.stopped

    first.unregister.side_effect = assert_stopped
    second.unregister.side_effect = assert_stopped
    manager = ProductManagerTest({"product": product})
    manager.start_products()
    getattr(manager, shutdown)(join=True)
    first.unregister.assert_called_once_with()
    second.unregister.assert_called_once_with()


@pytest.mark.parametrize("shutdown", ["stop_products", "exit_products"])
def test_bundle_unregistration_failure_does_not_block_cleanup(shutdown: str) -> None:
    product, other = BaseProduct(), BaseProduct()
    first, second, third = (Mock(spec=SubscriberBundle) for _ in range(3))
    second.unregister.side_effect = RuntimeError("unregister failed")
    product.subscriber_bundles = (first, second)
    other.subscriber_bundles = (third,)
    manager = ProductManagerTest({"other": other, "product": product})
    manager.start_products()
    getattr(manager, shutdown)()
    first.unregister.assert_called_once_with()
    second.unregister.assert_called_once_with()
    third.unregister.assert_called_once_with()
    assert product.stopped and other.stopped


@pytest.mark.parametrize("shutdown", ["stop_products", "exit_products"])
def test_disabled_product_still_unregisters_bundles(shutdown: str, monkeypatch: pytest.MonkeyPatch) -> None:
    product = BaseProduct()
    bundle = Mock(spec=SubscriberBundle)
    product.subscriber_bundles = (bundle,)
    manager = ProductManagerTest({"product": product})
    manager.start_products()
    monkeypatch.setattr(product, "enabled", Mock(return_value=False))
    getattr(manager, shutdown)()
    bundle.unregister.assert_called_once_with()
    assert not product.stopped


def test_skip_exit_retains_bundles_but_explicit_stop_unregisters(monkeypatch: pytest.MonkeyPatch) -> None:
    product = BaseProduct()
    bundle = Mock(spec=SubscriberBundle)
    product.subscriber_bundles = (bundle,)
    monkeypatch.setattr(product, "skip_exit", Mock(return_value=True), raising=False)
    manager = ProductManagerTest({"product": product})
    manager.start_products()
    manager.exit_products()
    bundle.unregister.assert_not_called()
    assert not product.stopped
    manager.stop_products()
    bundle.unregister.assert_called_once_with()
    assert product.stopped


@pytest.mark.parametrize("shutdown", ["stop_products", "exit_products"])
def test_failed_stop_retains_completion_listeners(shutdown: str, monkeypatch: pytest.MonkeyPatch) -> None:
    product = BaseProduct()
    bundle = Mock(spec=SubscriberBundle)
    product.subscriber_bundles = (bundle,)
    monkeypatch.setattr(product, "stop", Mock(side_effect=RuntimeError("drain failed")))
    manager = ProductManagerTest({"product": product})
    manager.start_products()
    getattr(manager, shutdown)()
    bundle.unregister.assert_not_called()


def test_product_without_bundle_attribute_keeps_existing_lifecycle() -> None:
    product = Mock(spec=("enabled", "start", "stop"))
    product.enabled.return_value = True
    manager = ProductManagerTest({"product": product})
    manager.start_products()
    product.start.assert_called_once_with()
    manager.stop_products()
    product.stop.assert_called_once_with(join=False)
