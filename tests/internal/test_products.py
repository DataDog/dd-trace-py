import sys
from types import ModuleType
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from ddtrace.internal.products import Product
from ddtrace.internal.products import ProductManager


class ProductManagerTest(ProductManager):
    def __init__(self, products, failed=set()) -> None:
        self._products = None
        self.__products__ = products
        self._failed = failed
        self._registered = {}
        self._integration_hooks = None


class BaseProduct(Product):
    requires = []

    def __init__(self) -> None:
        self.started = self.post_started = self.restarted = self.stopped = self.exited = self.post_preloaded = False

    def post_preload(self) -> None:
        self.post_preloaded = True

    def enabled(self) -> bool:
        return True

    def start(self) -> None:
        self.started = True

    def post_start(self) -> None:
        self.post_started = True

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
    assert a.post_started

    # b fails to start, so c won't start because it depends on b
    assert not b.started and not c.started
    assert not b.post_started and not c.post_started


def test_product_manager_start():
    a = BaseProduct()
    manager = ProductManagerTest({"a": a})
    manager.run_protocol()
    assert a.started
    assert a.post_started


def test_product_manager_post_start_runs_after_all_products_start():
    events = []

    class A(BaseProduct):
        def start(self) -> None:
            events.append("a-start")

        def post_start(self) -> None:
            events.append("a-post-start")

    class B(BaseProduct):
        requires = ["a"]

        def start(self) -> None:
            events.append("b-start")

        def post_start(self) -> None:
            events.append("b-post-start")

    manager = ProductManagerTest({"a": A(), "b": B()})
    manager.run_protocol()

    assert events == ["a-start", "b-start", "a-post-start", "b-post-start"]


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
            self._registered = {}
            self._integration_hooks = None

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

    assert broken.started and broken.post_started
    assert dependent.started and dependent.post_started
    telemetry_writer.product_activated.assert_called_once_with("dependent", True)


class SubscribingProduct(BaseProduct):
    def __init__(self, subscribers: list[MagicMock], activated: bool = True) -> None:
        super().__init__()
        self._subscribers = subscribers
        self._activated = activated

    def subscribers(self):
        return self._subscribers

    def activated(self) -> bool:
        return self._activated


def test_product_manager_registers_subscribers_once():
    subscriber = MagicMock()
    manager = ProductManagerTest({"foo": SubscribingProduct([subscriber])})

    manager.register_subscribers("foo")
    manager.register_subscribers("foo")
    subscriber.register.assert_called_once_with()

    manager.unregister_subscribers("foo")
    manager.unregister_subscribers("foo")
    subscriber.unregister.assert_called_once_with()


def test_product_manager_registers_subscribers_of_activated_products():
    active, inactive = MagicMock(), MagicMock()
    manager = ProductManagerTest(
        {
            "active": SubscribingProduct([active]),
            "inactive": SubscribingProduct([inactive], activated=False),
        }
    )

    manager.start_products()
    active.register.assert_called_once_with()
    inactive.register.assert_not_called()

    manager.stop_products()
    active.unregister.assert_called_once_with()
    inactive.unregister.assert_not_called()


def test_product_manager_reevaluates_products_without_subscribers():
    subscriber = MagicMock()
    subscribers = []
    manager = ProductManagerTest({"foo": SubscribingProduct(subscribers)})

    manager.register_subscribers("foo")
    subscribers.append(subscriber)
    manager.register_subscribers("foo")

    subscriber.register.assert_called_once_with()


def test_product_manager_unregisters_partially_registered_subscribers():
    registered, failing, skipped = MagicMock(), MagicMock(), MagicMock()
    failing.register.side_effect = RuntimeError
    manager = ProductManagerTest({"foo": SubscribingProduct([registered, failing, skipped])})

    manager.register_subscribers("foo")
    skipped.register.assert_not_called()

    manager.unregister_subscribers("foo")
    registered.unregister.assert_called_once_with()
    failing.unregister.assert_not_called()


def test_product_manager_ignores_products_without_subscribers_hook():
    manager = ProductManagerTest({"foo": BaseProduct()})

    manager.register_subscribers("foo")
    manager.register_subscribers("unknown")

    assert manager._registered == {}


def test_product_manager_notifies_integration_hooks():
    failing, hook = MagicMock(), MagicMock()
    failing.on_integration_patched.side_effect = RuntimeError
    manager = ProductManagerTest({})
    entry_points = [MagicMock(load=MagicMock(return_value=failing)), MagicMock(load=MagicMock(return_value=hook))]

    with (
        patch("ddtrace.internal.products.get_integration_hook_entry_points", return_value=entry_points),
        patch("ddtrace.internal.products.is_trusted", return_value=True),
    ):
        manager.integration_patched("flask")
        manager.integration_patched("redis")

    for entry_point in entry_points:
        entry_point.load.assert_called_once_with()
    failing.on_integration_patched.assert_called_with("redis")
    assert [c.args for c in hook.on_integration_patched.call_args_list] == [("flask",), ("redis",)]


def test_product_manager_loads_only_the_requested_product():
    subscriber = MagicMock()
    foo = MagicMock(load=MagicMock(return_value=SubscribingProduct([subscriber])))
    foo.name = "foo"
    bar = MagicMock()
    bar.name = "bar"
    manager = ProductManagerTest({})

    with (
        patch("ddtrace.internal.products.get_product_entry_points", return_value=[bar, foo]),
        patch("ddtrace.internal.products.is_trusted", return_value=True),
    ):
        manager.register_subscribers("foo")

    subscriber.register.assert_called_once_with()
    bar.load.assert_not_called()


def test_product_manager_skips_untrusted_products():
    foo = MagicMock()
    foo.name = "foo"
    manager = ProductManagerTest({})

    with (
        patch("ddtrace.internal.products.get_product_entry_points", return_value=[foo]),
        patch("ddtrace.internal.products.is_trusted", return_value=False),
    ):
        manager.register_subscribers("foo")

    foo.load.assert_not_called()


@pytest.mark.parametrize("patch_error", [None, RuntimeError("patch failed")])
def test_patching_an_integration_notifies_products(monkeypatch, patch_error):
    from ddtrace import _monkey

    integration = ModuleType("fake_integration_patch")
    integration.patch = MagicMock(side_effect=patch_error)
    monkeypatch.setitem(sys.modules, "fake_integration_patch", integration)

    with patch.object(_monkey.product_manager, "integration_patched") as integration_patched:
        _monkey._on_import_factory("fake_integration", "%s_patch", raise_errors=False)(MagicMock())

    if patch_error is None:
        integration_patched.assert_called_once_with("fake_integration")
    else:
        integration_patched.assert_not_called()
