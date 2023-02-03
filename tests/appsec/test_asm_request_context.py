import pytest

from ddtrace.appsec import _asm_request_context


@pytest.fixture(autouse=True)
def reset_test_asm_context():
    _asm_request_context.reset()
    yield
    _asm_request_context.reset()


_TEST_IP = "1.2.3.4"
_TEST_HEADERS = {"foo": "bar"}


def test_context_set_and_reset():
    _asm_request_context.asm_request_context_set(_TEST_IP, _TEST_HEADERS, True)
    assert _asm_request_context.get_ip() == _TEST_IP
    assert _asm_request_context.get_headers() == _TEST_HEADERS
    assert _asm_request_context.get_headers_case_sensitive()
    _asm_request_context.reset()
    assert _asm_request_context.get_ip() is None
    assert _asm_request_context.get_headers() is None
    assert not _asm_request_context.get_headers_case_sensitive()
    _asm_request_context.asm_request_context_set(_TEST_IP, _TEST_HEADERS)
    assert not _asm_request_context.get_headers_case_sensitive()


def test_set_get_ip():
    _asm_request_context.set_ip(_TEST_IP)
    assert _asm_request_context.get_ip() == _TEST_IP


def test_set_get_headers():
    _asm_request_context.set_headers(_TEST_HEADERS)
    assert _asm_request_context.get_headers() == _TEST_HEADERS


def test_set_get_headers_case_sensitive():
    # default reset value should be False
    assert not _asm_request_context.get_headers_case_sensitive()
    _asm_request_context.set_headers_case_sensitive(True)
    assert _asm_request_context.get_headers_case_sensitive()
    _asm_request_context.set_headers_case_sensitive(False)
    assert not _asm_request_context.get_headers_case_sensitive()


def test_asm_request_context_manager():
    with _asm_request_context.asm_request_context_manager(_TEST_IP, _TEST_HEADERS, True):
        assert _asm_request_context.get_ip() == _TEST_IP
        assert _asm_request_context.get_headers() == _TEST_HEADERS
        assert _asm_request_context.get_headers_case_sensitive()

    assert _asm_request_context.get_ip() is None
    assert _asm_request_context.get_headers() is None
    assert not _asm_request_context.get_headers_case_sensitive()
