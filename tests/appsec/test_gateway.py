from ddtrace.appsec._gateway import _Addresses
from ddtrace.span import _RequestStore


def test_gateway_flow(gateway):
    gateway.mark_needed(_Addresses.SERVER_RESPONSE_STATUS)
    assert not gateway.is_needed(_Addresses.SERVER_REQUEST_HEADERS_NO_COOKIES)
    assert gateway.needed_address_count == 1
    assert gateway.is_needed(_Addresses.SERVER_RESPONSE_STATUS)
    store = _RequestStore()
    data = {_Addresses.SERVER_RESPONSE_STATUS: "404"}
    gateway.propagate(store, data)
    assert store.kept_addresses[_Addresses.SERVER_RESPONSE_STATUS] == "404"


def test_gateway_clear(gateway):
    gateway.mark_needed(_Addresses.SERVER_RESPONSE_STATUS)
    store = _RequestStore()
    data = {_Addresses.SERVER_RESPONSE_STATUS: "404"}
    gateway.propagate(store, data)
    assert store.kept_addresses[_Addresses.SERVER_RESPONSE_STATUS] == "404"

    gateway.clear()

    store = _RequestStore()
    data = {_Addresses.SERVER_RESPONSE_STATUS: "404"}
    gateway.propagate(store, data)
    assert _Addresses.SERVER_RESPONSE_STATUS not in store.kept_addresses
