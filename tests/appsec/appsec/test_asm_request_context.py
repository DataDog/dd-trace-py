import logging

import pytest

from ddtrace.appsec import _asm_request_context
from ddtrace.internal._exceptions import BlockingException
from tests.appsec.utils import asm_context
from tests.utils import override_global_config


_TEST_IP = "1.2.3.4"
_TEST_HEADERS = {"foo": "bar"}

config_asm = {"_asm_enabled": True}


def test_context_set_and_reset():
    with asm_context(
        ip_addr=_TEST_IP,
        headers=_TEST_HEADERS,
        headers_case_sensitive=True,
        block_request_callable=(lambda: True),
        config=config_asm,
    ):
        assert _asm_request_context.get_ip() == _TEST_IP
        assert _asm_request_context.get_headers() == _TEST_HEADERS
        assert _asm_request_context.get_headers_case_sensitive()
        assert _asm_request_context.get_value("callbacks", "block") is not None
    assert _asm_request_context.get_ip() is None
    assert _asm_request_context.get_headers() == {}
    assert _asm_request_context.get_value("callbacks", "block") is None
    assert not _asm_request_context.get_headers_case_sensitive()
    with asm_context(
        ip_addr=_TEST_IP,
        headers=_TEST_HEADERS,
        config=config_asm,
    ):
        assert not _asm_request_context.get_headers_case_sensitive()
        assert not _asm_request_context.block_request()


def test_set_get_ip():
    with asm_context(config=config_asm):
        _asm_request_context.set_ip(_TEST_IP)
        assert _asm_request_context.get_ip() == _TEST_IP


def test_set_get_headers():
    with asm_context(config=config_asm):
        _asm_request_context.set_headers(_TEST_HEADERS)
        assert _asm_request_context.get_headers() == _TEST_HEADERS


def test_call_block_callable_none():
    with asm_context(config=config_asm):
        _asm_request_context.set_block_request_callable(None)
        assert not _asm_request_context.block_request()
    assert not _asm_request_context.block_request()


def test_call_block_callable_noargs():
    def _callable():
        return 42

    with asm_context(config=config_asm):
        _asm_request_context.set_block_request_callable(_callable)
        assert _asm_request_context.get_value("callbacks", "block")() == 42
    assert not _asm_request_context.get_value("callbacks", "block")


def test_call_block_callable_curried():
    class TestException(Exception):
        pass

    def _callable():
        raise TestException()

    with asm_context(config=config_asm):
        _asm_request_context.set_block_request_callable(_callable)
        with pytest.raises(TestException):
            assert _asm_request_context.block_request()


def test_set_get_headers_case_sensitive():
    # default reset value should be False
    assert not _asm_request_context.get_headers_case_sensitive()
    with asm_context(config=config_asm):
        _asm_request_context.set_headers_case_sensitive(True)
        assert _asm_request_context.get_headers_case_sensitive()
        _asm_request_context.set_headers_case_sensitive(False)
        assert not _asm_request_context.get_headers_case_sensitive()


def test_asm_request_context_manager():
    with asm_context(
        ip_addr=_TEST_IP,
        headers=_TEST_HEADERS,
        headers_case_sensitive=True,
        block_request_callable=(lambda: 42),
        config=config_asm,
    ):
        assert _asm_request_context.get_ip() == _TEST_IP
        assert _asm_request_context.get_headers() == _TEST_HEADERS
        assert _asm_request_context.get_headers_case_sensitive()
        assert _asm_request_context.get_value("callbacks", "block")() == 42

    assert _asm_request_context.get_ip() is None
    assert _asm_request_context.get_headers() == {}
    assert _asm_request_context.get_value("callbacks", "block") is None
    assert not _asm_request_context.get_headers_case_sensitive()


def test_blocking_exception_correctly_propagated():
    with asm_context(config=config_asm):
        witness = 0
        with asm_context(config=config_asm):
            witness = 1
            raise BlockingException({}, "rule", "type", "value")
            # should be skipped by exception
            witness = 3
        # should be also skipped by exception
        witness = 4
    # no more exception there
    # ensure that the exception was raised and caught at the end of the last context manager
    assert witness == 1


def test_log_waf_callback(caplog):
    import ddtrace.internal.logger as ddlogger

    with caplog.at_level(logging.WARNING), override_global_config({"_asm_enabled": True}):
        _asm_request_context.call_waf_callback()

    # warning log
    assert len(caplog.records) == 1, f"expected 1 log record, got {len(caplog.records)}: {caplog.records}"
    assert caplog.records[0].levelname == "WARNING"
    assert caplog.records[0].msg.startswith("appsec::asm_context::call_waf_callback::not_set")

    caplog.clear()
    # second time, the warning log should be filtered out
    with caplog.at_level(logging.WARNING), override_global_config({"_asm_enabled": True}):
        _asm_request_context.call_waf_callback()

    assert len(caplog.records) == 0, f"expected 0 log record, got {len(caplog.records)}: {caplog.records}"
    caplog.clear()
    # third time, the warning log should be not filtered out
    ddlogger.set_tag_rate_limit("asm_context::call_waf_callback::not_set", 0)
    with caplog.at_level(logging.WARNING), override_global_config({"_asm_enabled": True}):
        _asm_request_context.call_waf_callback()
        # warning log
    assert len(caplog.records) == 1, f"expected 1 log record, got {len(caplog.records)}: {caplog.records}"
    assert caplog.records[0].levelname == "WARNING"
    assert caplog.records[0].msg.startswith("appsec::asm_context::call_waf_callback::not_set")
    ddlogger.set_tag_rate_limit("asm_context::call_waf_callback::not_set", ddlogger.DAY)
