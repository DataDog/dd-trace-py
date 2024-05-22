import pytest

from ddtrace.appsec import _asm_request_context
from ddtrace.internal._exceptions import BlockingException
from tests.utils import override_global_config


_TEST_IP = "1.2.3.4"
_TEST_HEADERS = {"foo": "bar"}


def test_context_set_and_reset():
    with override_global_config({"_asm_enabled": True}):
        with _asm_request_context.asm_request_context_manager(_TEST_IP, _TEST_HEADERS, True, lambda: True):
            assert _asm_request_context.get_ip() == _TEST_IP
            assert _asm_request_context.get_headers() == _TEST_HEADERS
            assert _asm_request_context.get_headers_case_sensitive()
            assert _asm_request_context.get_value("callbacks", "block") is not None
        assert _asm_request_context.get_ip() is None
        assert _asm_request_context.get_headers() == {}
        assert _asm_request_context.get_value("callbacks", "block") is None
        assert not _asm_request_context.get_headers_case_sensitive()
        with _asm_request_context.asm_request_context_manager(_TEST_IP, _TEST_HEADERS):
            assert not _asm_request_context.get_headers_case_sensitive()
            assert not _asm_request_context.block_request()


def test_set_get_ip():
    with override_global_config({"_asm_enabled": True}):
        with _asm_request_context.asm_request_context_manager():
            _asm_request_context.set_ip(_TEST_IP)
            assert _asm_request_context.get_ip() == _TEST_IP


def test_set_get_headers():
    with override_global_config({"_asm_enabled": True}):
        with _asm_request_context.asm_request_context_manager():
            _asm_request_context.set_headers(_TEST_HEADERS)
            assert _asm_request_context.get_headers() == _TEST_HEADERS


def test_call_block_callable_none():
    with override_global_config({"_asm_enabled": True}):
        with _asm_request_context.asm_request_context_manager():
            _asm_request_context.set_block_request_callable(None)
            assert not _asm_request_context.block_request()
        assert not _asm_request_context.block_request()


def test_call_block_callable_noargs():
    def _callable():
        return 42

    with override_global_config({"_asm_enabled": True}):
        with _asm_request_context.asm_request_context_manager():
            _asm_request_context.set_block_request_callable(_callable)
            assert _asm_request_context.get_value("callbacks", "block")() == 42
        assert not _asm_request_context.get_value("callbacks", "block")


def test_call_block_callable_curried():
    class TestException(Exception):
        pass

    def _callable():
        raise TestException()

    with override_global_config({"_asm_enabled": True}):
        with _asm_request_context.asm_request_context_manager():
            _asm_request_context.set_block_request_callable(_callable)
            with pytest.raises(TestException):
                assert _asm_request_context.block_request()


def test_set_get_headers_case_sensitive():
    # default reset value should be False
    assert not _asm_request_context.get_headers_case_sensitive()
    with override_global_config({"_asm_enabled": True}):
        with _asm_request_context.asm_request_context_manager():
            _asm_request_context.set_headers_case_sensitive(True)
            assert _asm_request_context.get_headers_case_sensitive()
            _asm_request_context.set_headers_case_sensitive(False)
            assert not _asm_request_context.get_headers_case_sensitive()


def test_asm_request_context_manager():
    with override_global_config({"_asm_enabled": True}):
        with _asm_request_context.asm_request_context_manager(_TEST_IP, _TEST_HEADERS, True, lambda: 42):
            assert _asm_request_context.get_ip() == _TEST_IP
            assert _asm_request_context.get_headers() == _TEST_HEADERS
            assert _asm_request_context.get_headers_case_sensitive()
            assert _asm_request_context.get_value("callbacks", "block")() == 42

    assert _asm_request_context.get_ip() is None
    assert _asm_request_context.get_headers() == {}
    assert _asm_request_context.get_value("callbacks", "block") is None
    assert not _asm_request_context.get_headers_case_sensitive()


def test_blocking_exception_correctly_propagated():
    with override_global_config({"_asm_enabled": True}):
        with _asm_request_context.asm_request_context_manager():
            witness = 0
            with _asm_request_context.asm_request_context_manager():
                witness = 1
                raise BlockingException({}, "rule", "type", "value")
                # should be skipped by exception
                witness = 3
            # should be also skipped by exception
            witness = 4
        # no more exception there
        # ensure that the exception was raised and caught at the end of the last context manager
        assert witness == 1
