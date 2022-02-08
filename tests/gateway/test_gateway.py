from ddtrace.gateway import Addresses


def test_gateway_flow(gateway):
    gateway.mark_needed(ADDRESSES.SERVER_RESPONSE_STATUS.value)
    assert not gateway.is_needed(ADDRESSES.SERVER_REQUEST_HEADERS_NO_COOKIES.value)
    assert gateway.needed_address_count == 1
    assert gateway.is_needed(ADDRESSES.SERVER_RESPONSE_STATUS.value)
    store = {}
    data = {ADDRESSES.SERVER_RESPONSE_STATUS.value: "404"}
    gateway.propagate(store, data)
    assert store["kept_addresses"][ADDRESSES.SERVER_RESPONSE_STATUS.value] == "404"


def test_gateway_clear(gateway):
    gateway.mark_needed(ADDRESSES.SERVER_RESPONSE_STATUS.value)
    store = {}
    data = {ADDRESSES.SERVER_RESPONSE_STATUS.value: "404"}
    gateway.propagate(store, data)
    assert store["kept_addresses"][ADDRESSES.SERVER_RESPONSE_STATUS.value] == "404"

    gateway.clear()

    store = {}
    data = {ADDRESSES.SERVER_RESPONSE_STATUS.value: "404"}
    gateway.propagate(store, data)
    assert ADDRESSES.SERVER_RESPONSE_STATUS.value not in store["kept_addresses"]
