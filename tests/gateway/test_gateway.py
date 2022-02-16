from ddtrace.gateway import Addresses
from ddtrace.span import RequestStore


def test_gateway_flow(gateway):
    gateway.mark_needed(Addresses.SERVER_RESPONSE_STATUS.value)
    assert not gateway.is_needed(Addresses.SERVER_REQUEST_HEADERS_NO_COOKIES.value)
    assert gateway.needed_address_count == 1
    assert gateway.is_needed(Addresses.SERVER_RESPONSE_STATUS.value)
    store = RequestStore()
    data = {Addresses.SERVER_RESPONSE_STATUS.value: "404"}
    gateway.propagate(store, data)
    assert store.kept_Addresses[Addresses.SERVER_RESPONSE_STATUS.value] == "404"


def test_gateway_clear(gateway):
    gateway.mark_needed(Addresses.SERVER_RESPONSE_STATUS.value)
    store = RequestStore()
    data = {Addresses.SERVER_RESPONSE_STATUS.value: "404"}
    gateway.propagate(store, data)
    assert store.kept_Addresses[Addresses.SERVER_RESPONSE_STATUS.value] == "404"

    gateway.clear()

    store = RequestStore()
    data = {Addresses.SERVER_RESPONSE_STATUS.value: "404"}
    gateway.propagate(store, data)
    assert Addresses.SERVER_RESPONSE_STATUS.value not in store.kept_Addresses
