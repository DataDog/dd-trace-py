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


def test_product_manager_restart():
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


def test_product_manager_is_enabled():
    class ProductWithConfig:
        def __init__(self, enabled):
            self.config = type("Config", (), {"enabled": enabled})()

    # Test when product doesn't exist
    manager = ProductManagerTest({})
    assert not manager.is_enabled("nonexistent")

    # Test when product exists but has no config
    product_no_config = BaseProduct()
    manager = ProductManagerTest({"no_config": product_no_config})
    assert not manager.is_enabled("no_config")

    # Test when product exists and is enabled
    enabled_product = ProductWithConfig(True)
    manager = ProductManagerTest({"enabled": enabled_product})
    assert manager.is_enabled("enabled")

    # Test when product exists but is disabled
    disabled_product = ProductWithConfig(False)
    manager = ProductManagerTest({"disabled": disabled_product})
    assert not manager.is_enabled("disabled")
