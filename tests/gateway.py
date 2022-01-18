import os.path

from ddtrace.gateway.gateway import Subscription


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def test_simple_gateway_case(gateway):
    class TestSubscription(Subscription):
        def __init__(self):
            self.called = False

        def run(self, store, new_addresses):
            self.called = True
            assert "foo" in new_addresses
            assert store["addresses"]["foo"] == "bar"

    sub = TestSubscription()
    gateway.subscribe(["foo"], sub)
    gateway.propagate({"addresses": {}, "meta": {}}, {"foo": "bar"})
    assert sub.called


def test_clear_gateway(gateway):
    class TestSubscription(Subscription):
        def __init__(self):
            self.called = False

        def run(self, store, new_addresses):
            self.called = True
            assert "foo" in new_addresses
            assert store["addresses"]["foo"] == "bar"

    sub = TestSubscription()
    gateway.subscribe(["foo"], sub)
    gateway.clear()
    gateway.propagate({"addresses": {}, "meta": {}}, {"foo": "bar"})
    assert not sub.called
